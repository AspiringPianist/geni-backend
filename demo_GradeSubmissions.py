import google.generativeai as genai
import PyPDF2, os, tempfile, random
from dotenv import load_dotenv
from typing import List, Dict
import firebase_admin
from firebase_admin import credentials, firestore
import chromadb, re, json
from sentence_transformers import SentenceTransformer
import numpy as np

# Import the ChromaDB path from uplaod_assignment.py
from demo_uploadAssignment import CHROMA_DB_PATH

student_feedback_marks: Dict[str, Dict[str, str]] = {}

def read_pdf(file_path):
    """Reads text from a PDF file."""
    try:
        with open(file_path, "rb") as file:
            reader = PyPDF2.PdfReader(file)
            text = ""
            for page in reader.pages:
                text += page.extract_text() or ""
            return text
    except Exception as e:
        print(f"Error reading PDF: {e}")
        return None

class AssignmentChecker:
    def __init__(self):
        load_dotenv()
        api_key = os.getenv('GEMINI_API')
        if not api_key:
            raise ValueError("Gemini api key isnt present ")
        genai.configure(api_key=api_key)
        self.gemini_model = genai.GenerativeModel('gemini-2.0-flash')
        self.embedding_model = SentenceTransformer('all-MiniLM-L6-v2')

        cred = credentials.Certificate("tibby-teach-firebase-adminsdk-fbsvc-a51c5b7b7b.json")
        if not firebase_admin._apps:
            firebase_admin.initialize_app(cred)
        self.db = firestore.client()
        self.submissions_collection = self.db.collection('submissions')

        # Initializing chromadb
        self.chroma_client = chromadb.PersistentClient(path=CHROMA_DB_PATH)
        self.collection_name = "student_submissions"
        self.collection = self.chroma_client.get_or_create_collection(name=self.collection_name)
        self.expected_embedding_dimension = None

        # Assignment details
        self.assignment_details = {}  # Store questions, answers, marking scheme

    def load_assignment_details(self, questions_pdf_path, answers_pdf_path):
        """Loads assignment details (questions, answers, marking scheme) from PDFs using Gemini."""
        try:
            questions_text = read_pdf(questions_pdf_path)
            answers_text = read_pdf(answers_pdf_path)

            if not questions_text or not answers_text:
                raise ValueError("Could not extract text from one or both PDFs.")

            prompt = f"""
            You are an expert assistant tasked with extracting structured information from a document.
            Here is the text from the assignment questions PDF:
            {questions_text}

            Here is the text from the answer key PDF:
            {answers_text}

            Your task is to:
            1. Identify all the questions in the questions PDF.
            2. Identify the corresponding answers for each question in the answers PDF.
            3. If there is a marking scheme, extract it and associate it with the correct question.
            4. Return the data in a structured JSON format like this:
            {{
                "questions": [
                    {{
                        "question": "What is the capital of France?",
                        "answer": "The capital of France is Paris.",
                        "marks": 2,
                        "marking_scheme": "1 mark for naming Paris, 1 mark for correct spelling."
                    }},
                    {{
                        "question": "Explain the theory of relativity.",
                        "answer": "The theory of relativity, developed by Albert Einstein...",
                        "marks": 5,
                        "marking_scheme": "2 marks for explaining special relativity, 3 marks for explaining general relativity."
                    }},
                    // ... more questions ...
                ]
            }}
            If there is no marking scheme, then dont include it.
            """

            response = self.gemini_model.generate_content(prompt)
            gemini_response = response.text
            try:
                start_index = gemini_response.find('{')
                end_index = gemini_response.rfind('}') + 1
                json_str = gemini_response[start_index:end_index]
                data = json.loads(json_str)
                self.assignment_details = data
                print("Assignment details loaded successfully.")
            except json.JSONDecodeError as e:
                print(f"Error decoding JSON from Gemini response: {e}")
                print(f"Gemini's response: {gemini_response}")
                self.assignment_details = {}
                raise ValueError("Could not extract structured data from Gemini's response.")

        except Exception as e:
            print(f"Error loading assignment details: {e}")
            self.assignment_details = {}

    def process_all_submissions(self, assignment_id: str) -> Dict:
        """Process all submissions for a specific assignment.
        
        Args:
            assignment_id: The ID of the assignment to process
            
        Returns:
            Dictionary containing results for all submissions
        """
        try:
            # Query submissions for this assignment
            submissions = self.submissions_collection.where('assignment_id', '==', assignment_id).stream()
            
            results = {}
            for submission in submissions:
                submission_data = submission.to_dict()
                submission_id = submission.id
                
                try:
                    # Process the submission
                    result = self.process_submission(submission_id)
                    if result:
                        results[submission_id] = {
                            'submission_id': submission_id,
                            'student_id': submission_data.get('student_id'),
                            'assignment_id': submission_data.get('assignment_id'),
                            'feedback': result.get('feedback', ''),
                            'mark': result.get('mark', ''),
                            'status': 'success'
                        }
                    else:
                        results[submission_id] = {
                            'submission_id': submission_id,
                            'student_id': submission_data.get('student_id'),
                            'assignment_id': submission_data.get('assignment_id'),
                            'status': 'error',
                            'error': 'Failed to process submission'
                        }
                except Exception as e:
                    results[submission_id] = {
                        'submission_id': submission_id,
                        'student_id': submission_data.get('student_id'),
                        'assignment_id': submission_data.get('assignment_id'),
                        'status': 'error',
                        'error': str(e)
                    }
            
            return {
                'assignment_id': assignment_id,
                'total_submissions': len(results),
                'results': results
            }
            
        except Exception as e:
            print(f"Error processing all submissions: {e}")
            return {
                'assignment_id': assignment_id,
                'status': 'error',
                'error': str(e)
            }

    def process_submission(self, submission_id: str) -> Dict:
        """Process a single submission and generate feedback.
        
        Args:
            submission_id: The ID of the submission to process
            
        Returns:
            Dictionary containing feedback and marks
        """
        try:
            # Get submission from Firestore
            submission_doc = self.submissions_collection.document(submission_id).get()
            if not submission_doc.exists:
                raise ValueError(f"Submission {submission_id} not found")
                
            submission_data = submission_doc.to_dict()
            submission_text = submission_data.get('submission_text')
            
            if not submission_text:
                raise ValueError("No submission text found")
            
            # Process the submission using Gemini
            prompt = f"""
            You are an expert educator evaluating a student's submission.
            Here are the assignment questions and answers:
            {self.assignment_details}
            
            Here is the student's submission:
            {submission_text}
            
            Evaluate the submission and provide feedback in this exact format:
            
            Feedback: [Provide detailed feedback for the submission]
            Total Marks: [X/Y] (where Y is the total marks for the assignment)
            """
            
            response = self.gemini_model.generate_content(prompt)
            feedback_text = response.text
            
            # Parse the feedback to extract marks and feedback
            feedback = ""
            mark = ""
            for line in feedback_text.split('\n'):
                line = line.strip()
                if not line:
                    continue
                    
                if line.startswith('Feedback:'):
                    feedback = line.replace('Feedback:', '').strip()
                elif line.startswith('Mark:'):
                    mark = line.replace('Mark:', '').strip()
            
            # Update Firestore with feedback and marks
            self.submissions_collection.document(submission_id).update({
                'feedback': feedback,
                'mark': mark,
                'processed': True,
                'processed_at': firestore.SERVER_TIMESTAMP
            })
            
            return {
                'feedback': feedback,
                'mark': mark,
                'submission_id': submission_id
            }
            
        except Exception as e:
            print(f"Error processing submission {submission_id}: {e}")
            return None

    def get_submission_status(self, submission_id: str) -> Dict:
        """Get the current status of a submission.
        
        Args:
            submission_id: The ID of the submission to check
            
        Returns:
            Dictionary containing submission status
        """
        try:
            submission_doc = self.submissions_collection.document(submission_id).get()
            if not submission_doc.exists:
                return {
                    'status': 'error',
                    'error': 'Submission not found'
                }
                
            submission_data = submission_doc.to_dict()
            return {
                'status': 'success',
                'submission_id': submission_id,
                'student_id': submission_data.get('student_id'),
                'assignment_id': submission_data.get('assignment_id'),
                'processed': submission_data.get('processed', False),
                'feedback': submission_data.get('feedback', ''),
                'mark': submission_data.get('mark', '')
            }
            
        except Exception as e:
            return {
                'status': 'error',
                'error': str(e)
            }
