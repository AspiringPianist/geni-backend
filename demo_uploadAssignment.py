import google.generativeai as genai
import PyPDF2, os, tempfile, random
from dotenv import load_dotenv
from typing import List, Dict
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
import io, time
import shutil
import chromadb
from sentence_transformers import SentenceTransformer
import firebase_admin
from firebase_admin import credentials, firestore
from datetime import datetime
import re
from firebase_admin import firestore
import numpy as np

# Define the ChromaDB path in a single place
CHROMA_DB_PATH = os.path.abspath("./chroma_db")

class UploadAssignment:
    def __init__(self):
        load_dotenv()
        api_key = os.getenv('GEMINI_API')
        if not api_key:
            raise ValueError("Gemini api key not found")
        genai.configure(api_key = api_key)
        self.model = genai.GenerativeModel('gemini-2.0-flash')
        self.embedding_model = SentenceTransformer('all-MiniLM-L6-v2') # Initialize SentenceTransformer

        # Initialize Firebase
        cred = credentials.Certificate("tibby-teach-firebase-adminsdk-fbsvc-a51c5b7b7b.json")
        if not firebase_admin._apps:
            firebase_admin.initialize_app(cred)
        self.db = firestore.client()
        self.submissions_collection = self.db.collection('submissions')

        #Initialising chromadb
        self.chroma_client = chromadb.PersistentClient(path=CHROMA_DB_PATH)
        self.collection_name = "student_submissions"
        self.collection = self.chroma_client.get_or_create_collection(name=self.collection_name)
        self.expected_embedding_dimension = 384 # Set the expected dimension to 384

    def upload_submission(self, file_path, assignment_id, student_id):
        """
        Uploads a student submission to Firestore and ChromaDB.

        Args:
            file_path (str): Path to the submission file.
            assignment_id (str): ID of the assignment.
            student_id (str): ID of the student.

        Returns:
            str: Firestore document ID, or None if there was an error.
        """
        try:
            # Read the file content
            with open(file_path, "rb") as file:
                if file_path.lower().endswith('.pdf'):
                    pdf_reader = PyPDF2.PdfReader(file)
                    text = ""
                    for page in pdf_reader.pages:
                        text += page.extract_text() or ""
                else:
                    content = file.read()
                    # Try to decode as UTF-8, fall back to binary if that fails
                    try:
                        text = content.decode('utf-8')
                    except UnicodeDecodeError:
                        # If UTF-8 decode fails, use a binary-safe encoding
                        text = content.decode('latin-1')

            # Generate embeddings using SentenceTransformer
            embeddings = self.embedding_model.encode(text).tolist() # Use SentenceTransformer to generate embeddings

            # Pad or truncate embeddings to match the expected dimension
            if len(embeddings) < self.expected_embedding_dimension:
                print(f"Padding embedding from {len(embeddings)} to {self.expected_embedding_dimension}")
                embeddings.extend([0.0] * (self.expected_embedding_dimension - len(embeddings)))
            elif len(embeddings) > self.expected_embedding_dimension:
                print(f"Truncating embedding from {len(embeddings)} to {self.expected_embedding_dimension}")
                embeddings = embeddings[:self.expected_embedding_dimension]

            # Metadata for Firestore
            metadata = {
                "assignment_id": assignment_id,
                "student_id": student_id,
                "file_path": file_path,
                "submission_text": text,
                "timestamp": datetime.now()
            }

            # Add to Firestore
            doc_ref = self.submissions_collection.document()
            doc_ref.set(metadata)
            firestore_doc_id = doc_ref.id

            # Add to ChromaDB
            chroma_metadata = {
                "assignment_id": assignment_id,
                "student_id": student_id,
                "firestore_doc_id": firestore_doc_id
            }
            self.collection.add(
                documents=[text],
                embeddings=[embeddings],
                metadatas=[chroma_metadata],
                ids=[firestore_doc_id]
            )

            print(f"File from {file_path} saved to Firestore with ID: {firestore_doc_id} and ChromaDB.")
            time.sleep(1)
            return firestore_doc_id

        except Exception as e:
            print(f"Error uploading submission: {e}")
            return None
