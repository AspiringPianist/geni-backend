import firebase_admin
from firebase_admin import credentials, auth, firestore
from fastapi import FastAPI, Header, HTTPException, Depends, Response, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from typing import Dict, Any, List, Optional
from pydantic import BaseModel
from datetime import datetime
from visual_summary import generate_visual_summary_json
import logging
import uvicorn
from quiz import generate_quiz_json
from call_gemini import generate_response
from demo_coursemap import CourseGenerator
from demo_GradeSubmissions import AssignmentChecker
import os
import tempfile
import shutil
from demo_uploadAssignment import UploadAssignment
from demo_assignmentgenerator import AssignmentGenerator
from fastapi.responses import FileResponse, JSONResponse
# Pip installs:
# pip install firebase-admin fastapi uvicorn pydantic

# Initialize Firebase Admin SDK
cred = credentials.Certificate("tibby-teach-firebase-adminsdk-fbsvc-a51c5b7b7b.json")
firebase_admin.initialize_app(cred)

db = firestore.client()
app = FastAPI()
generator = AssignmentGenerator()
assignment_cache = {}
# Configure CORS
origins = ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
# Pydantic Models for Request/Response Data
class User(BaseModel):
    role: str
    email: str
    name: str

class FileData(BaseModel):
    fileName: str
    fileType: str
    jsonData: Dict[str, Any]
    chatId: Optional[str] = None

class Chat(BaseModel):
    title: str

class Message(BaseModel):
    text: str
    chatId: str

class VisualSummaryRequest(BaseModel):
    topic: str
    rag: str = ""  # Made optional with default

class QuizRequest(BaseModel):
    topic: str
    rag: str = ""  # Optional with default

class ChatRequest(BaseModel):
    chatId: str
    userMessage: str

class CourseMapRequest(BaseModel):
    topic: str
    difficulty: str
    duration: str
    assignments: str
    assessment_type: str
    teaching_mode: str
    instructor: str = ""

class AssignmentDetailsRequest(BaseModel):
    assignment_id: str
    questions_pdf_path: str
    answers_pdf_path: str

class ProcessSubmissionsRequest(BaseModel):
    assignment_id: str

class SubmissionStatusRequest(BaseModel):
    submission_id: str

class UploadSubmissionRequest(BaseModel):
    assignment_id: str
    student_id: str

class AssignmentGenerationRequest(BaseModel):
    topic: str
    difficulty: str
    duration: str
    num_questions: int
    learning_objectives: Optional[str] = None
    additional_requirements: Optional[str] = None
    custom_duration: Optional[str] = None

def verify_token(id_token: str) -> Dict[str, Any]:
    try:
        decoded_token = auth.verify_id_token(id_token)
        return decoded_token
    except Exception as e:
        raise HTTPException(status_code=401, detail="Invalid token")

def get_user_id(authorization: str = Header(None)) -> str:
    print(authorization)
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization header missing")

    id_token = authorization.split("Bearer ")[1]
    decoded_token = verify_token(id_token)
    return decoded_token["uid"]


@app.post("/chat_with_memory/")
async def chat_with_memory(request: ChatRequest, user_id: str = Depends(get_user_id)):
    chat_ref = db.collection("chats").document(request.chatId)
    messages_ref = chat_ref.collection("messages").order_by("timestamp").limit(10)  # Fetch last 10 messages
    
    # Load recent messages
    messages = []
    for msg in messages_ref.stream():
        msg_data = msg.to_dict()
        messages.append({
            "role": "user" if msg_data["senderId"] == user_id else "bot",
            "text": msg_data["text"]
        })

    # Prepare prompt for Gemini (rolling window)
    chat_history = "\n".join([f"{m['role']}: {m['text']}" for m in messages[-10:]])
    user_prompt = f"{chat_history}\nUser: {request.userMessage}\nAssistant:"

    # Call Gemini API
    response = generate_response(user_prompt)

    # Save AI-generated response in Firestore under messages subcollection
    new_message_ref = chat_ref.collection("messages").document()
    new_message_ref.set({
        "senderId": "ai",
        "text": response.text,
        "timestamp": firestore.SERVER_TIMESTAMP,
        "ragMetadata": {},  # No RAG, so keeping it empty
        "retrievalAugmentedGeneration": "",
        "generatedFileId": None
    })

    return {"response": response.text}


@app.post("/users/", response_model=Dict[str, str])
async def create_user(user: User, user_id: str = Depends(get_user_id)):
    user_data = user.model_dump()
    db.collection("users").document(user_id).set(user_data)
    return {"message": "User created successfully"}

# React Example:
# async function createUser(userData, idToken) {
#   const response = await fetch('/users/', {
#     method: 'POST',
#     headers: {
#       'Content-Type': 'application/json',
#       'Authorization': `Bearer ${idToken}`
#     },
#     body: JSON.stringify(userData)
#   });
#   const data = await response.json();
#   return data;
# }

@app.get("/users/{user_id}", response_model=User)
async def get_user(user_id: str):
    user_doc = db.collection("users").document(user_id).get()
    if not user_doc.exists:
        raise HTTPException(status_code=404, detail="User not found")
    return User(**user_doc.to_dict())

# React Example:
# async function getUser(userId, idToken) {
#   const response = await fetch(`/users/${userId}`, {
#     headers: {
#       'Authorization': `Bearer ${idToken}`
#     }
#   });
#   const data = await response.json();
#   return data;
# }

@app.get("/files/list/", response_model=List[Dict[str, Any]])
async def get_user_files(user_id: str = Depends(get_user_id)):
    """
    Fetch all files created by the authenticated user.
    Returns a list of file objects with fileId and other metadata.
    """
    try:
        logger.info(f"Fetching files for user: {user_id}")
        files_ref = db.collection("files").where("userId", "==", user_id).stream()
        files = [{"fileId": file.id, **file.to_dict()} for file in files_ref]
        logger.info(f"Found {len(files)} files")
        return files
    except Exception as e:
        logger.error(f"Error fetching files: {str(e)}")
        raise HTTPException(status_code=500, detail="Error fetching files")

@app.post("/files/", response_model=Dict[str, str])
async def upload_file(file: FileData, user_id: str = Depends(get_user_id)):
    file_data = file.model_dump()
    file_data["userId"] = user_id
    file_data["uploadTimestamp"] = firestore.SERVER_TIMESTAMP
    file_ref = db.collection("files").document()
    file_id = file_ref.id
    file_ref.set(file_data)
    return {"fileId": file_id}

# React Example:
# async function uploadFile(fileData, idToken) {
#   const response = await fetch('/files/', {
#     method: 'POST',
#     headers: {
#       'Content-Type': 'application/json',
#       'Authorization': `Bearer ${idToken}`
#     },
#     body: JSON.stringify(fileData)
#   });
#   const data = await response.json();
#   return data;
# }

@app.get("/files/{file_id}", response_model=Dict[str, Any])
async def get_file_by_id(file_id: str, user_id: str = Depends(get_user_id)):
    """
    Fetch a specific file by ID. Also verifies that the requesting user owns the file.
    """
    try:
        file_doc = db.collection("files").document(file_id).get()
        if not file_doc.exists:
            raise HTTPException(status_code=404, detail="File not found")
            
        file_data = file_doc.to_dict()
        # Verify user owns this file
        if file_data["userId"] != user_id:
            raise HTTPException(status_code=403, detail="Not authorized to access this file")
            
        return {"fileId": file_id, **file_data}
    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"Error fetching file {file_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Error fetching file")

# React Example:
# async function getFile(fileId, idToken) {
#   const response = await fetch(`/files/${fileId}`, {
#     headers: {
#       'Authorization': `Bearer ${idToken}`
#     }
#   });
#   const data = await response.json();
#   return data;
# }

@app.post("/chats/", response_model=Dict[str, str])
async def create_chat(chat: Chat, user_id: str = Depends(get_user_id)):
    chat_data = chat.model_dump()
    chat_data["userId"] = user_id
    chat_data["startTimestamp"] = firestore.SERVER_TIMESTAMP
    chat_ref = db.collection("chats").document()
    chat_id = chat_ref.id
    chat_ref.set(chat_data)
    return {"chatId": chat_id}

# React Example:
# async function createChat(chatData, idToken) {
#   const response = await fetch('/chats/', {
#     method: 'POST',
#     headers: {
#       'Content-Type': 'application/json',
#       'Authorization': `Bearer ${idToken}`
#     },
#     body: JSON.stringify(chatData)
#   });
#   const data = await response.json();
#   return data;
# }

@app.get("/chats/", response_model=List[dict])
async def get_user_chats(user_id: str = Depends(get_user_id)):
    """
    Fetch all chats created by the authenticated user.
    Returns a list of chat objects with chatId, title, and startTimestamp.
    """
    chats_ref = db.collection("chats").where("userId", "==", user_id).stream()
    chats = [{"chatId": chat.id, **chat.to_dict()} for chat in chats_ref]
    if not chats:
        return []  # Return empty list if no chats exist
    return chats



@app.get("/chats/{chat_id}", response_model=Chat)
async def get_chat(chat_id: str):
    chat_doc = db.collection("chats").document(chat_id).get()
    if not chat_doc.exists:
        raise HTTPException(status_code=404, detail="Chat not found")
    return Chat(**chat_doc.to_dict())

# React Example:
# async function getChat(chatId, idToken) {
#   const response = await fetch(`/chats/${chatId}`, {
#     headers: {
#       'Authorization': `Bearer ${idToken}`
#     }
#   });
#   const data = await response.json();
#   return data;
# }

@app.post("/messages/", response_model=Dict[str, str])
async def send_message(message: Message, user_id: str = Depends(get_user_id)):
    message_data = message.model_dump()
    message_data["senderId"] = user_id
    message_data["timestamp"] = firestore.SERVER_TIMESTAMP
    chat_ref = db.collection("chats").document(message_data["chatId"])
    message_ref = chat_ref.collection("messages").document()
    message_id = message_ref.id
    message_ref.set(message_data)
    return {"messageId": message_id}

# React Example:
# async function sendMessage(messageData, idToken) {
#   const response = await fetch('/messages/', {
#     method: 'POST',
#     headers: {
#       'Content-Type': 'application/json',
#       'Authorization': `Bearer ${idToken}`
#     },
#     body: JSON.stringify(messageData)
#   });
#   const data = await response.json();
#   return data;
# }
@app.get("/")
async def hello_world():
    return {"message": "Hello, World!"}

@app.get("/messages/{chat_id}", response_model=List[Dict[str, Any]])
async def get_messages(chat_id: str):
    messages_ref = db.collection("chats").document(chat_id).collection("messages").order_by("timestamp")
    messages = []
    for message_doc in messages_ref.stream():
        messages.append(message_doc.to_dict())
    return messages

# React Example:
# async function getMessages(chatId, idToken) {
#   const response = await fetch(`/messages/${chatId}`, {
#     headers: {
#       'Authorization': `Bearer ${idToken}`
#     }
#   });
#   const data = await response.json();
#   return data;
# }

####################################################### Learning Aids ######################################################################

@app.post("/visualsummary/")
async def visualsummary(request: VisualSummaryRequest, user_id: str = Depends(get_user_id)):
    logger.info(f"Received request: topic={request.topic}, rag={request.rag}")
    topic = request.topic
    visual_summary = generate_visual_summary_json(topic, request.rag)
    file_ref = db.collection("files").document()
    file_ref.set({
        "userId": user_id,
        "fileName": f"{topic}_visual_summary.json",
        "fileType": "ai_generated",
        "jsonData": visual_summary,
        "uploadTimestamp": firestore.SERVER_TIMESTAMP
    })
    response = {"fileId": file_ref.id, "jsonData": visual_summary}
    logger.info(f"Returning response: {response}")
    return response

@app.post("/quiz/")
async def generate_quiz(request: QuizRequest, user_id: str = Depends(get_user_id)):
    logger.info(f"Received quiz request: topic={request.topic}, rag={request.rag}")
    topic = request.topic
    quiz_data = generate_quiz_json(topic, request.rag)
    file_ref = db.collection("files").document()
    file_ref.set({
        "userId": user_id,
        "fileName": f"{topic}_quiz.json",
        "fileType": "ai_generated",
        "jsonData": quiz_data,
        "uploadTimestamp": firestore.SERVER_TIMESTAMP
    })
    response = {"fileId": file_ref.id, "jsonData": quiz_data}
    logger.info(f"Returning quiz response: {response}")
    return response

@app.patch("/files/{file_id}", response_model=Dict[str, str])
async def update_file(file_id: str, file: FileData, user_id: str = Depends(get_user_id)):
    print(f"Received PATCH request for file: {file_id}")
    print(f"Payload: {file.model_dump_json()}")
    file_doc = db.collection("files").document(file_id).get()
    if not file_doc.exists:
        raise HTTPException(status_code=404, detail="File not found")
    if file_doc.to_dict()["userId"] != user_id:
        raise HTTPException(status_code=403, detail="Not authorized to update this file")
    
    file_data = file.model_dump(exclude_unset=True)
    db.collection("files").document(file_id).update(file_data)
    return {"message": "File updated successfully"}

@app.post("/api/generate-course-map/")
async def generate_course_map(request: CourseMapRequest, user_id: str = Depends(get_user_id)):
    try:
        course_generator = CourseGenerator()
        course_details = request.model_dump()
        
        # Generate the curriculum
        pdf_path = course_generator.generate_curriculum(course_details)
        
        # Read the generated PDF file
        with open(pdf_path, 'rb') as f:
            pdf_content = f.read()
            
        # Clean up the temporary file
        os.remove(pdf_path)
        
        # Return the PDF file
        return Response(
            content=pdf_content,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'attachment; filename="{request.topic.replace(" ", "_")}_curriculum.pdf"'
            }
        )
    except Exception as e:
        logger.error(f"Error generating course map: {str(e)}")
        raise HTTPException(status_code=500, detail="Error generating course map")

@app.post("/api/load-assignment-details/")
async def load_assignment_details(request: AssignmentDetailsRequest, user_id: str = Depends(get_user_id)):
    try:
        assignment_checker = AssignmentChecker()
        assignment_checker.load_assignment_details(request.questions_pdf_path, request.answers_pdf_path)
        
        # Store the assignment details in Firestore
        db.collection("assignments").document(request.assignment_id).set({
            "questions_pdf_path": request.questions_pdf_path,
            "answers_pdf_path": request.answers_pdf_path,
            "details_loaded": True,
            "loaded_by": user_id,
            "loaded_at": firestore.SERVER_TIMESTAMP
        })
        
        return {"status": "success", "message": "Assignment details loaded successfully"}
    except Exception as e:
        logger.error(f"Error loading assignment details: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error loading assignment details: {str(e)}")

@app.post("/api/process-submissions/")
async def process_submissions(request: ProcessSubmissionsRequest, user_id: str = Depends(get_user_id)):
    try:
        assignment_checker = AssignmentChecker()
        results = assignment_checker.process_all_submissions(request.assignment_id)
        
        # Store the results in Firestore
        db.collection("assignment_results").document(request.assignment_id).set({
            "results": results,
            "processed_by": user_id,
            "processed_at": firestore.SERVER_TIMESTAMP
        })
        
        return results
    except Exception as e:
        logger.error(f"Error processing submissions: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error processing submissions: {str(e)}")

@app.post("/api/submission-status/")
async def get_submission_status(request: SubmissionStatusRequest, user_id: str = Depends(get_user_id)):
    try:
        assignment_checker = AssignmentChecker()
        status = assignment_checker.get_submission_status(request.submission_id)
        return status
    except Exception as e:
        logger.error(f"Error getting submission status: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error getting submission status: {str(e)}")

@app.post("/api/upload-pdf/")
async def upload_pdf(
    file: UploadFile = File(...),
    type: str = Form(...),
    assignment_id: str = Form(...),
    user_id: str = Depends(get_user_id)
):
    # Validate required fields
    if not file or not type or not assignment_id:
        raise HTTPException(status_code=400, detail="Missing file, type, or assignment_id")
    
    if file.filename == '':
        raise HTTPException(status_code=400, detail="No selected file")
    
    # Validate file type
    if not file.filename.lower().endswith('.pdf'):
        raise HTTPException(status_code=400, detail="File must be a PDF")
    
    # Validate assignment type
    if type not in ['questions', 'answers']:
        raise HTTPException(status_code=400, detail="Type must be 'questions' or 'answers'")
    
    temp_file_path = None
    try:
        # Create a temporary file to store the uploaded PDF
        with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as temp_file:
            shutil.copyfileobj(file.file, temp_file)
            temp_file_path = temp_file.name
        
        # Check if assignment document exists, create if not
        assignment_ref = db.collection("assignments").document(assignment_id)
        assignment_doc = assignment_ref.get()
        
        if not assignment_doc.exists:
            # Create the assignment document if it doesn't exist
            assignment_ref.set({
                "created_by": user_id,
                "created_at": firestore.SERVER_TIMESTAMP
            })
        
        # Store the file path in Firestore
        file_path = f"assignments/{assignment_id}/{type}_{file.filename}"
        assignment_ref.update({
            f"{type}_pdf_path": file_path,
            f"{type}_uploaded_at": firestore.SERVER_TIMESTAMP,
            f"{type}_uploaded_by": user_id
        })
        
        # Clean up the temporary file
        if temp_file_path and os.path.exists(temp_file_path):
            os.unlink(temp_file_path)
        
        return {"status": "success", "file_path": file_path}
    
    except Exception as e:
        # Log the error for debugging
        logging.error(f"Error uploading PDF: {str(e)}")
        
        # Clean up the temporary file if it exists
        if temp_file_path and os.path.exists(temp_file_path):
            os.unlink(temp_file_path)
        
        # Return appropriate error message
        if "No such file or directory" in str(e):
            raise HTTPException(
                status_code=500, 
                detail="Server configuration error: Missing required files. Please contact the administrator."
            )
        else:
            raise HTTPException(status_code=500, detail=f"Error uploading PDF: {str(e)}")

@app.get("/api/assignments/")
async def get_assignments(user_id: str = Depends(get_user_id)):
    try:
        # Get all assignments where the user is either the creator or has access
        assignments_ref = db.collection("assignments").where("created_by", "==", user_id).stream()
        assignments = []
        
        for doc in assignments_ref:
            assignment_data = doc.to_dict()
            assignment_data["id"] = doc.id
            assignments.append(assignment_data)
            
        return assignments
    except Exception as e:
        logger.error(f"Error fetching assignments: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error fetching assignments: {str(e)}")

@app.get("/api/submissions/{assignment_id}")
async def get_submissions(assignment_id: str, user_id: str = Depends(get_user_id)):
    try:
        # Get all submissions for the given assignment
        submissions_ref = db.collection("submissions").where("assignment_id", "==", assignment_id).stream()
        submissions = []
        
        for doc in submissions_ref:
            submission_data = doc.to_dict()
            submission_data["id"] = doc.id
            submissions.append(submission_data)
            
        return submissions
    except Exception as e:
        logger.error(f"Error fetching submissions: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error fetching submissions: {str(e)}")

@app.post("/api/upload-submission/")
async def upload_submission(
    file: UploadFile = File(...),
    assignment_id: str = Form(...),
    student_id: str = Form(...),
    user_id: str = Depends(get_user_id)
):
    # Validate required fields
    if not assignment_id or not student_id:
        raise HTTPException(status_code=400, detail="Missing assignment_id or student_id")
    
    temp_file_path = None
    try:
        # Create a temporary file to store the upload
        with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as temp_file:
            shutil.copyfileobj(file.file, temp_file)
            temp_file_path = temp_file.name
        
        # Initialize UploadAssignment and process the submission
        upload_handler = UploadAssignment()
        doc_id = upload_handler.upload_submission(
            file_path=temp_file_path,
            assignment_id=assignment_id,
            student_id=student_id
        )
        
        # Clean up the temporary file
        if temp_file_path and os.path.exists(temp_file_path):
            os.unlink(temp_file_path)
        
        if doc_id is None:
            raise HTTPException(status_code=500, detail="Error uploading submission")
        
        return {"message": "Submission uploaded successfully", "submission_id": doc_id}
    
    except Exception as e:
        # Log the error for debugging
        logging.error(f"Error in upload_submission: {str(e)}")
        
        # Clean up the temporary file if it exists
        if temp_file_path and os.path.exists(temp_file_path):
            os.unlink(temp_file_path)
        
        # Return appropriate error message
        if "No such file or directory" in str(e):
            raise HTTPException(
                status_code=500, 
                detail="Server configuration error: Missing required files. Please contact the administrator."
            )
        else:
            raise HTTPException(status_code=500, detail=f"Error uploading submission: {str(e)}")

@app.post("/api/process-all-submissions/")
async def process_all_submissions(
    assignment_id: str = Form(...),
    user_id: str = Depends(get_user_id)
):
    # Validate required fields
    if not assignment_id:
        raise HTTPException(status_code=400, detail="assignment_id is required")
    
    try:
        # Initialize AssignmentChecker and process all submissions
        checker = AssignmentChecker()
        result = checker.process_all_submissions(assignment_id)
        
        # Store the results in Firestore
        db.collection("assignment_results").document(assignment_id).set({
            "results": result,
            "processed_by": user_id,
            "processed_at": firestore.SERVER_TIMESTAMP
        })
        
        return result
    except Exception as e:
        # Log the error for debugging
        logging.error(f"Error processing all submissions: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error processing submissions: {str(e)}")


@app.post("/api/generate-assignment/")
async def generate_assignment(
    topic: str = Form(...),
    difficulty: str = Form(...),
    duration: str = Form(...),
    num_questions: int = Form(...),
    learning_objectives: Optional[str] = Form(None),
    additional_requirements: Optional[str] = Form(None),
    custom_duration: Optional[str] = Form(None),
    pdf_file: Optional[UploadFile] = File(None),
    user_id: str = Depends(get_user_id)
):
    # Handle custom duration
    if duration == 'custom' and custom_duration:
        duration = custom_duration
    
    # Save uploaded PDF if provided
    pdf_path = None
    if pdf_file and pdf_file.filename.endswith('.pdf'):
        with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as temp_file:
            content = await pdf_file.read()
            temp_file.write(content)
            pdf_path = temp_file.name
    
    # Create question details based on number of questions
    question_details = [{"type": "TEXT", "marks": 100 // num_questions} for _ in range(num_questions)]
    
    # Generate the assignment
    result = generator.generate_assignment(
        topic=topic,
        credentials_file_path=None,
        question_details=question_details,
        pdf_file=pdf_path,
        duration=f"{duration} minutes",
        difficulty=difficulty,
        learning_objectives=learning_objectives,
        additional_requirements=additional_requirements
    )
    
    # Clean up the temporary PDF file if it exists
    if pdf_path and os.path.exists(pdf_path):
        os.unlink(pdf_path)
    
    if not result:
        raise HTTPException(status_code=500, detail="Failed to generate assignment")
    
    # Store the assignment in cache with user ID
    assignment_id = f"{user_id}_{len(assignment_cache)}"
    assignment_cache[assignment_id] = result
    
    # Return the result with the assignment ID
    return {
        "assignment_id": assignment_id,
        **result
    }

@app.get("/api/download-assignment-pdf/")
async def download_assignment_pdf(
    assignment_id: str,
    include_answers: bool = False,
    user_id: str = Depends(get_user_id)
):
    # Check if the assignment exists in cache
    if assignment_id not in assignment_cache:
        raise HTTPException(status_code=404, detail="Assignment not found")
    
    # Get the assignment data
    assignment_data = assignment_cache[assignment_id]
    
    # Generate the PDF
    pdf_path = generator.create_pdf(assignment_data, include_answers)
    if not pdf_path:
        raise HTTPException(status_code=500, detail="Failed to generate PDF")
    
    # Create a descriptive filename
    pdf_type = "full_assignment" if include_answers else "questions_only"
    filename = f"{assignment_data['topic'].replace(' ', '_')}_{pdf_type}.pdf"
    
    # Return the PDF file
    return FileResponse(
        path=pdf_path,
        media_type="application/pdf",
        filename=filename
    )

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=5049)