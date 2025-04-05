import firebase_admin
from firebase_admin import credentials, auth, firestore
from fastapi import FastAPI, Header, HTTPException, Depends, UploadFile
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
# Pip installs:
# pip install firebase-admin fastapi uvicorn pydantic

# Initialize Firebase Admin SDK
cred = credentials.Certificate("tibby-teach-firebase-adminsdk-fbsvc-a51c5b7b7b.json")
firebase_admin.initialize_app(cred)

db = firestore.client()
app = FastAPI()

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

    # Save to Firestore
    assignment_ref = db.collection("classrooms").document(classroom_id).collection("assignments").document()
    assignment_ref.set({
        "title": assignment.topic,
        "description": result.get("content", {}).get("description", ""),
        "dueDate": None,  # Teacher can set this later
        "createdAt": firestore.SERVER_TIMESTAMP,
        "totalPoints": 100,
        "questions": result.get("content", {}).get("questions", [])
    })
    
    return {"id": assignment_ref.id, **result}

@app.post("/api/classrooms/{classroom_id}/assignments/{assignment_id}/submit")
async def submit_assignment(
    classroom_id: str,
    assignment_id: str,
    file: UploadFile,
    user_id: str = Depends(get_user_id)
):
    # Create temporary file
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
            # Update assignment submission status
            db.collection("classrooms").document(classroom_id)\
              .collection("assignments").document(assignment_id)\
              .set({
                  f"submissions.{user_id}": {
                      "submittedAt": firestore.SERVER_TIMESTAMP,
                      "status": "submitted",
                      "fileUrl": tmp_path  # In production, this would be a cloud storage URL
                  }
              }, merge=True)
            
            return {"status": "success", "submissionId": submission_id}
        else:
            raise HTTPException(status_code=500, detail="Failed to upload submission")
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

@app.post("/api/classrooms/{classroom_id}/assignments/{assignment_id}/grade")
async def grade_assignment(
    classroom_id: str,
    assignment_id: str,
    user_id: str = Depends(get_user_id)
):
    # Verify user is teacher
    classroom = db.collection("classrooms").document(classroom_id).get()
    if not classroom.exists or classroom.to_dict()["teacherId"] != user_id:
        raise HTTPException(status_code=403, detail="Not authorized")
    
    checker = AssignmentChecker()
    results = checker.process_all_submissions(assignment_id)
    
    # Update grades in Firestore
    assignment_ref = db.collection("classrooms").document(classroom_id)\
                      .collection("assignments").document(assignment_id)
    
    for submission_id, result in results.get("results", {}).items():
        if result.get("status") == "success":
            assignment_ref.set({
                f"submissions.{result['student_id']}": {
                    "status": "graded",
                    "grade": float(result["mark"].split("/")[0]),  # Convert "X/Y" to number
                    "feedback": result["feedback"]
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

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=5049)