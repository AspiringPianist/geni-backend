import firebase_admin
from firebase_admin import credentials, auth, firestore
from fastapi import FastAPI, Header, HTTPException, Depends, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from typing import Dict, Any, List, Optional
from pydantic import BaseModel
from datetime import datetime
from visual_summary import generate_visual_summary_json
import logging
import uvicorn
from quiz import generate_quiz_json
from call_gemini import generate_response
from demo_assignment_generator import AssignmentGenerator
from demo_GradeSubmissions import AssignmentChecker
from demo_uploadAssignment import UploadAssignment
import tempfile
import os
import random
import string
import json
from sentence_transformers import SentenceTransformer, util
import torch
# Pip installs:
# pip install firebase-admin fastapi uvicorn pydantic

# Initialize Firebase Admin SDK
try:
    # Get credentials from environment variable
    firebase_creds_json = os.environ.get('FIREBASE_ADMIN_CREDENTIALS')
    if not firebase_creds_json:
        # Fallback to local file for development
        cred = credentials.Certificate("tibby-teach-firebase-adminsdk-fbsvc-a51c5b7b7b.json")
    else:
        # Use credentials from environment variable
        cred_dict = json.loads(firebase_creds_json)
        cred = credentials.Certificate(cred_dict)
    
    firebase_admin.initialize_app(cred)
except Exception as e:
    print(f"Failed to initialize Firebase: {str(e)}")
    raise

db = firestore.client()
app = FastAPI()

# Update CORS settings
origins = [
    "http://localhost:5173",  # Development
    "https://geni-frontend-green.vercel.app",  # Replace with your Vercel URL
    "https://tibbymvp-production.up.railway.app"  # Add your Railway domain
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

try:
    model = SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')
except Exception as e:
    logger.error(f"Failed to load sentence transformer model: {str(e)}")
    model = None

# Pydantic Models for Request/Response Data
class User(BaseModel):
    role: str
    email: str
    name: str
    teachingClassrooms: List[str] = []  # Default empty list for teachers
    enrolledClassrooms: List[str] = []   # Default empty list for students

class File(BaseModel):
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

class AssignmentGenerationRequest(BaseModel):
    topic: str
    num_questions: int
    duration: int
    difficulty: str
    learning_objectives: List[str]
    additional_requirements: Optional[str] = None

class CreateClassroomRequest(BaseModel):
    name: str
    description: str

class JoinClassroomRequest(BaseModel):
    code: str

class GradeRequest(BaseModel):
    useAI: bool = False


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
    # Initialize classroom arrays based on role
    if user_data["role"] == "teacher":
        user_data["teachingClassrooms"] = []
    else:
        user_data["enrolledClassrooms"] = []
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
    print(user_doc.to_dict())
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
async def upload_file(file: File, user_id: str = Depends(get_user_id)):
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
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "version": "1.0.0",
        "service": "Tibby Backend API"
    }

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
async def update_file(file_id: str, file: File, user_id: str = Depends(get_user_id)):
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

# Classroom Routes
@app.get("/api/classrooms/{classroom_id}")
async def get_classroom(classroom_id: str, user_id: str = Depends(get_user_id)):
    classroom_doc = db.collection("classrooms").document(classroom_id).get()
    if not classroom_doc.exists:
        raise HTTPException(status_code=404, detail="Classroom not found")
    
    classroom_data = classroom_doc.to_dict()
    # Check if user has access (is teacher or enrolled student)
    if (classroom_data["teacherId"] != user_id and 
        user_id not in classroom_data.get("students", {})):
        raise HTTPException(status_code=403, detail="Not authorized")
    
    return {"id": classroom_id, **classroom_data}

@app.get("/api/classrooms/{classroom_id}/assignments")
async def get_classroom_assignments(classroom_id: str, user_id: str = Depends(get_user_id)):
    assignments_ref = db.collection("classrooms").document(classroom_id).collection("assignments")
    assignments = []
    for doc in assignments_ref.stream():
        assignment_data = doc.to_dict()
        # Add classroom ID and assignment ID to each assignment
        assignments.append({
            "id": doc.id,
            "classroomId": classroom_id,
            **assignment_data
        })
    return assignments

@app.post("/api/classrooms/{classroom_id}/assignments")
async def create_classroom_assignment(
    classroom_id: str,
    assignment: AssignmentGenerationRequest,
    user_id: str = Depends(get_user_id)
):
    # Verify user is teacher
    classroom = db.collection("classrooms").document(classroom_id).get()
    if not classroom.exists or classroom.to_dict()["teacherId"] != user_id:
        raise HTTPException(status_code=403, detail="Not authorized")
    
    generator = AssignmentGenerator()
    question_details = [{"type": "TEXT", "marks": 100 // assignment.num_questions} for _ in range(assignment.num_questions)]
    
    result = generator.generate_assignment(
        topic=assignment.topic,
        credentials_file_path=None,
        question_details=question_details,
        duration=f"{assignment.duration} minutes",
        difficulty=assignment.difficulty,
        learning_objectives=assignment.learning_objectives,
        additional_requirements=assignment.additional_requirements
    )
    
    if not result:
        raise HTTPException(status_code=500, detail="Failed to generate assignment")

    # Save to Firestore with submissionCount
    assignment_ref = db.collection("classrooms").document(classroom_id).collection("assignments").document()
    assignment_ref.set({
        "title": assignment.topic,
        "description": result.get("content", {}).get("description", ""),
        "dueDate": None,  # Teacher can set this later
        "createdAt": firestore.SERVER_TIMESTAMP,
        "totalPoints": 100,
        "questions": result.get("content", {}).get("questions", []),
        "submissionCount": 0  # Initialize submission count
    })
    
    return {"id": assignment_ref.id, **result}

@app.post("/api/classrooms/{classroom_id}/assignments/{assignment_id}/submit")
async def submit_assignment(
    classroom_id: str,
    assignment_id: str,
    answer_text: str = Form(...),
    file: Optional[UploadFile] = None,
    user_id: str = Depends(get_user_id)
):
    # Verify classroom and assignment exist
    classroom_ref = db.collection("classrooms").document(classroom_id)
    assignment_ref = classroom_ref.collection("assignments").document(assignment_id)
    
    if not classroom_ref.get().exists:
        raise HTTPException(status_code=404, detail="Classroom not found")
    if not assignment_ref.get().exists:
        raise HTTPException(status_code=404, detail="Assignment not found")

    # Check if student has already submitted
    assignment_doc = assignment_ref.get()
    assignment_data = assignment_doc.to_dict()
    submissions = assignment_data.get("submissions", {})
    
    is_resubmission = user_id in submissions
    
    # Parse the answers JSON
    try:
        answers = json.loads(answer_text)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid answer format")
    
    submission_data = {
        "submittedAt": firestore.SERVER_TIMESTAMP,
        "status": "pending_review",
        "answerText": answer_text,
        "answers": answers,  # Store structured answers
        "grade": None,
        "feedback": None,
        "studentId": user_id,
    }

    submission_id = None
    # Handle file upload if provided
    if file:
        with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tmp:
            content = await file.read()
            tmp.write(content)
            tmp_path = tmp.name

        try:
            upload_handler = UploadAssignment()
            submission_id = upload_handler.upload_submission(
                file_path=tmp_path,
                assignment_id=assignment_id,
                student_id=user_id
            )
            if submission_id:
                submission_data["fileUrl"] = tmp_path  # In production, this would be a cloud storage URL
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    # Update the submission in a subcollection for better organization
    submission_ref = assignment_ref.collection("submissions").document(user_id)
    submission_ref.set(submission_data)

    # Update the assignment metadata
    batch = db.batch()
    if not is_resubmission:
        batch.update(assignment_ref, {
            "submissionCount": firestore.Increment(1),
            f"submissions.{user_id}": {
                "submittedAt": firestore.SERVER_TIMESTAMP,
                "status": "pending_review"
            }
        })
    
    batch.commit()
    
    return {"status": "success", "submissionId": user_id}

@app.post("/api/classrooms/{classroom_id}/assignments/{assignment_id}/grade")
async def grade_assignment(
    classroom_id: str,
    assignment_id: str,
    request: GradeRequest,
    user_id: str = Depends(get_user_id)
):
    # Verify user is teacher
    classroom = db.collection("classrooms").document(classroom_id).get()
    if not classroom.exists or classroom.to_dict()["teacherId"] != user_id:
        raise HTTPException(status_code=403, detail="Not authorized")
    
    if request.useAI:
        if not model:
            raise HTTPException(
                status_code=500, 
                detail="AI grading is not available - model failed to load"
            )
            
        try:
            # Get assignment and submissions
            assignment_ref = db.collection("classrooms").document(classroom_id)\
                              .collection("assignments").document(assignment_id)
            assignment_doc = assignment_ref.get()
            assignment_data = assignment_doc.to_dict()
            
            submissions_ref = assignment_ref.collection("submissions")
            submissions = {doc.id: doc.to_dict() for doc in submissions_ref.stream()}
            
            results = {"status": "success", "results": {}}
            
            # Grade each submission
            for student_id, submission in submissions.items():
                if submission.get("status") != "graded":
                    answers = submission.get("answers", {})
                    total_score = 0
                    feedback = []
                    
                    for q_idx, question in enumerate(assignment_data["questions"]):
                        student_answer = answers.get(str(q_idx), "").strip()
                        if not student_answer:
                            continue
                            
                        # Calculate similarity score using the model
                        answer_embedding = model.encode([student_answer])
                        question_embedding = model.encode([question["question_text"]])
                        similarity = float(util.pytorch_cos_sim(answer_embedding, question_embedding)[0][0])
                        
                        # Convert similarity to score (0-100)
                        question_score = int(similarity * question["marks"])
                        total_score += question_score
                        
                        feedback.append(f"Q{q_idx + 1}: {question_score}/{question['marks']} - " + 
                                     ("Good understanding shown." if similarity > 0.8 else 
                                      "Partial understanding shown." if similarity > 0.5 else 
                                      "Review this topic."))
                    
                    results["results"][student_id] = {
                        "status": "success",
                        "student_id": student_id,
                        "mark": f"{total_score}/100",
                        "feedback": "\n".join(feedback)
                    }
            
            # Update submissions with AI grades
            for submission_id, result in results["results"].items():
                if result["status"] == "success":
                    grade = float(result["mark"].split("/")[0])
                    batch = db.batch()
                    
                    # Update main submission status
                    batch.update(assignment_ref, {
                        f"submissions.{submission_id}.status": "graded",
                        f"submissions.{submission_id}.grade": grade,
                        f"submissions.{submission_id}.feedback": result["feedback"],
                        f"submissions.{submission_id}.gradedBy": "AI"
                    })
                    
                    # Update submission in subcollection
                    submission_ref = submissions_ref.document(submission_id)
                    batch.update(submission_ref, {
                        "status": "graded",
                        "grade": grade,
                        "feedback": result["feedback"],
                        "gradedBy": "AI"
                    })
                    
                    batch.commit()
            
            return results
                    
        except Exception as e:
            logger.error(f"AI grading error: {str(e)}")
            raise HTTPException(status_code=500, detail=f"AI grading failed: {str(e)}")
    else:
        # Manual review mode - just mark as ready for review
        assignment_ref = db.collection("classrooms").document(classroom_id)\
                        .collection("assignments").document(assignment_id)
        assignment_data = assignment_ref.get().to_dict()
        results = {
            "status": "success",
            "results": {
                submission_id: {
                    "status": "pending_review",
                    "student_id": student_id,
                }
                for student_id, submission_id in assignment_data.get("submissions", {}).items()
            }
        }
    
    # Update grades in Firestore
    assignment_ref = db.collection("classrooms").document(classroom_id)\
                      .collection("assignments").document(assignment_id)
    
    for submission_id, result in results.get("results", {}).items():
        if result.get("status") == "success":
            assignment_ref.set({
                f"submissions.{result['student_id']}": {
                    "status": "graded" if request.useAI else "pending_review",
                    "grade": float(result["mark"].split("/")[0]) if request.useAI else None,
                    "feedback": result["feedback"] if request.useAI else None,
                    "gradedBy": "AI" if request.useAI else None
                }
            }, merge=True)
    
    return results

@app.get("/api/classrooms/{classroom_id}/messages")
async def get_classroom_messages(classroom_id: str, user_id: str = Depends(get_user_id)):
    classroom_ref = db.collection("classrooms").document(classroom_id)
    messages_ref = classroom_ref.collection("chats").order_by("timestamp", direction=firestore.Query.DESCENDING).limit(100)
    
    messages = []
    for doc in messages_ref.stream():
        messages.append(doc.to_dict())
    
    return messages

@app.post("/api/classrooms/{classroom_id}/messages")
async def send_classroom_message(
    classroom_id: str,
    message: Message,
    user_id: str = Depends(get_user_id)
):
    classroom_ref = db.collection("classrooms").document(classroom_id)
    message_ref = classroom_ref.collection("chats").document()
    
    message_data = {
        "text": message.text,
        "senderId": user_id,
        "timestamp": firestore.SERVER_TIMESTAMP,
        "type": "user"
    }
    
    message_ref.set(message_data)
    return message_data

def convert_timestamp(obj):
    """Convert Firestore ServerTimestamp to string format"""
    if isinstance(obj, dict):
        return {k: convert_timestamp(v) for k, v in obj.items()}
    elif hasattr(obj, '_seconds'):  # Check if it's a Timestamp object
        return obj.isoformat() if hasattr(obj, 'isoformat') else str(obj)
    return obj

@app.post("/api/classrooms")
async def create_classroom(
    request: CreateClassroomRequest,
    user_id: str = Depends(get_user_id)
):
    # Verify user is a teacher
    user_doc = db.collection("users").document(user_id).get()
    if not user_doc.exists or user_doc.to_dict()["role"] != "teacher":
        raise HTTPException(status_code=403, detail="Only teachers can create classrooms")
    
    # Create classroom with unique join code
    code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
    classroom_ref = db.collection("classrooms").document()
    classroom_data = {
        "name": request.name,
        "description": request.description,
        "teacherId": user_id,
        "joinCode": code,
        "createdAt": firestore.SERVER_TIMESTAMP,
        "students": {}
    }
    
    classroom_ref.set(classroom_data)
    
    # Add classroom to teacher's list
    db.collection("users").document(user_id).update({
        "teachingClassrooms": firestore.ArrayUnion([classroom_ref.id])
    })
    
    # Get the created classroom data
    created_classroom = classroom_ref.get()
    response_data = {
        "id": classroom_ref.id,
        **convert_timestamp(created_classroom.to_dict() or {})
    }
    
    return response_data

@app.post("/api/classrooms/join")
async def join_classroom(
    request: JoinClassroomRequest,
    user_id: str = Depends(get_user_id)
):
    # Find classroom by join code
    classrooms = db.collection("classrooms")\
                   .where("joinCode", "==", request.code)\
                   .limit(1)\
                   .stream()
    
    classroom = next((doc for doc in classrooms), None)
    if not classroom:
        raise HTTPException(status_code=404, detail="Invalid classroom code")
    
    classroom_id = classroom.id
    classroom_data = classroom.to_dict()
    
    # Check if user is already in classroom
    if user_id in classroom_data.get("students", {}):
        raise HTTPException(status_code=400, detail="Already enrolled in this classroom")
    
    # Get user data
    user = db.collection("users").document(user_id).get()
    if not user.exists:
        raise HTTPException(status_code=404, detail="User not found")
    user_data = user.to_dict()
    
    # Add student to classroom
    db.collection("classrooms").document(classroom_id).update({
        f"students.{user_id}": {
            "joinedAt": firestore.SERVER_TIMESTAMP,
            "name": user_data["name"],
            "email": user_data["email"]
        }
    })
    
    # Add classroom to student's enrolled list
    db.collection("users").document(user_id).update({
        "enrolledClassrooms": firestore.ArrayUnion([classroom_id])
    })
    
    return {"id": classroom_id, **classroom_data}

@app.get("/api/submissions/{assignment_id}")
async def get_submissions(
    assignment_id: str,
    classroom_id: str,  # Now a required query parameter
    user_id: str = Depends(get_user_id)
):
    try:
        # Get classroom and verify teacher access
        classroom_ref = db.collection("classrooms").document(classroom_id)
        classroom = classroom_ref.get()
        if not classroom.exists:
            raise HTTPException(status_code=404, detail="Classroom not found")
        
        if classroom.to_dict()["teacherId"] != user_id:
            raise HTTPException(status_code=403, detail="Only teachers can view all submissions")

        # Get assignment submissions
        assignment_ref = classroom_ref.collection("assignments").document(assignment_id)
        assignment_doc = assignment_ref.get()
        
        if not assignment_doc.exists:
            raise HTTPException(status_code=404, detail="Assignment not found")
        
        assignment_data = assignment_doc.to_dict()
        submissions = assignment_data.get("submissions", {})
        
        # Format submissions with student details
        formatted_submissions = []
        for student_id, submission in submissions.items():
            student_ref = db.collection("users").document(student_id).get()
            if student_ref.exists:
                student_data = student_ref.to_dict()
                formatted_submissions.append({
                    "id": student_id,
                    "student_name": student_data.get("name", "Unknown"),
                    "student_email": student_data.get("email", "Unknown"),
                    "submissionDate": submission.get("submittedAt"),
                    "status": submission.get("status", "pending_review"),
                    "grade": submission.get("grade"),
                    "feedback": submission.get("feedback"),
                    "answerText": submission.get("answerText"),
                    "fileUrl": submission.get("fileUrl"),
                    "gradedBy": submission.get("gradedBy")
                })
        
        return formatted_submissions
    except Exception as e:
        logger.error(f"Error fetching submissions: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/download-assignment-pdf")
async def download_assignment_pdf(
    assignment_id: str,
    include_answers: bool = False,
    user_id: str = Depends(get_user_id)
):
    """Download assignment as PDF"""
    try:
        # Get assignment data from Firestore
        assignment_ref = db.collection("assignments").document(assignment_id)
        assignment_doc = assignment_ref.get()
        
        if not assignment_doc.exists:
            raise HTTPException(status_code=404, detail="Assignment not found")
            
        assignment_data = assignment_doc.to_dict()
        
        # Check permissions
        classroom_ref = db.collection("classrooms").document(assignment_data["classroom_id"])
        classroom_doc = classroom_ref.get()
        classroom_data = classroom_doc.to_dict()
        
        if not classroom_data:
            raise HTTPException(status_code=404, detail="Classroom not found")
            
        # Only teachers can see answers
        if include_answers and classroom_data["teacherId"] != user_id:
            raise HTTPException(status_code=403, detail="Not authorized to view answers")
        
        # Generate PDF using the AssignmentGenerator
        generator = AssignmentGenerator()
        pdf_path = generator.create_pdf(assignment_data, include_answers)
        
        if not pdf_path:
            raise HTTPException(status_code=500, detail="Failed to generate PDF")
        
        # Create descriptive filename
        filename = f"{assignment_data['title'].replace(' ', '_')}_{assignment_id}.pdf"
        
        return FileResponse(
            path=pdf_path,
            media_type="application/pdf",
            filename=filename
        )
    except Exception as e:
        logger.error(f"Error generating PDF: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/classrooms/{classroom_id}/assignments/{assignment_id}")
async def get_assignment(
    classroom_id: str,
    assignment_id: str,
    user_id: str = Depends(get_user_id)
):
    # Get the assignment document
    assignment_ref = db.collection("classrooms").document(classroom_id)\
                      .collection("assignments").document(assignment_id)
    assignment_doc = assignment_ref.get()

    if not assignment_doc.exists:
        raise HTTPException(status_code=404, detail="Assignment not found")

    # Get classroom to verify access
    classroom = db.collection("classrooms").document(classroom_id).get()
    if not classroom.exists:
        raise HTTPException(status_code=404, detail="Classroom not found")

    classroom_data = classroom.to_dict()
    # Verify user has access (is teacher or enrolled student)
    if (classroom_data["teacherId"] != user_id and 
        user_id not in classroom_data.get("students", {})):
        raise HTTPException(status_code=403, detail="Not authorized")

    return {
        "id": assignment_id,
        "classroomId": classroom_id,
        **assignment_doc.to_dict()
    }

@app.post("/api/classrooms/{classroom_id}/assignments/{assignment_id}/submissions/{student_id}/grade")
async def grade_submission(
    classroom_id: str,
    assignment_id: str,
    student_id: str,
    grade_data: dict,
    user_id: str = Depends(get_user_id)
):
    try:
        # Verify teacher access
        classroom = db.collection("classrooms").document(classroom_id).get()
        if not classroom.exists or classroom.to_dict()["teacherId"] != user_id:
            raise HTTPException(status_code=403, detail="Only teachers can grade submissions")

        # Update submission grade
        assignment_ref = db.collection("classrooms").document(classroom_id)\
                          .collection("assignments").document(assignment_id)
        submission_ref = assignment_ref.collection("submissions").document(student_id)

        # Update both submission locations (main document and subcollection)
        batch = db.batch()
        
        # Update main submission status
        batch.update(assignment_ref, {
            f"submissions.{student_id}.status": "graded",
            f"submissions.{student_id}.grade": grade_data["grade"],
            f"submissions.{student_id}.feedback": grade_data["feedback"],
            f"submissions.{student_id}.gradedBy": "teacher"
        })

        # Update detailed submission
        batch.update(submission_ref, {
            "status": "graded",
            "grade": grade_data["grade"],
            "feedback": grade_data["feedback"],
            "gradedBy": "teacher"
        })

        batch.commit()
        return {"status": "success"}
    except Exception as e:
        logger.error(f"Error grading submission: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/classrooms/{classroom_id}/assignments/{assignment_id}/submissions/{student_id}")
async def get_submission_details(
    classroom_id: str,
    assignment_id: str,
    student_id: str,
    user_id: str = Depends(get_user_id)
):
    """Get detailed submission information for a specific student"""
    try:
        # Verify classroom access
        classroom = db.collection("classrooms").document(classroom_id).get()
        if not classroom.exists:
            raise HTTPException(status_code=404, detail="Classroom not found")
        
        classroom_data = classroom.to_dict()
        is_teacher = classroom_data["teacherId"] == user_id
        
        # Only allow teachers or the submission owner to view
        if not (is_teacher or user_id == student_id):
            raise HTTPException(status_code=403, detail="Not authorized to view this submission")

        # Get assignment and submission
        assignment_ref = db.collection("classrooms").document(classroom_id)\
                          .collection("assignments").document(assignment_id)
        submission_ref = assignment_ref.collection("submissions").document(student_id)
        
        submission_doc = submission_ref.get()
        if not submission_doc.exists:
            raise HTTPException(status_code=404, detail="Submission not found")
        
        submission_data = submission_doc.to_dict()

        # Get student details
        student_ref = db.collection("users").document(student_id)
        student_doc = student_ref.get()
        student_data = student_doc.to_dict() if student_doc.exists else {}

        # Combine submission data with student info
        return {
            "id": student_id,
            "student_name": student_data.get("name", "Unknown"),
            "student_email": student_data.get("email", "Unknown"),
            **submission_data
        }

    except Exception as e:
        logger.error(f"Error fetching submission details: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5049))
    uvicorn.run(app, host="0.0.0.0", port=port)