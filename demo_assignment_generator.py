import google.generativeai as genai
import PyPDF2
from dotenv import load_dotenv
import os
import re
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib import colors
from reportlab.lib.units import inch
import json
def read_pdf(file_path):
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

class AssignmentGenerator:
    def __init__(self):
        load_dotenv()
        api_key = os.getenv('GEMINI_API')
        if not api_key:
            raise ValueError("GEMINI_API is not set in the environment variables.")
        genai.configure(api_key=api_key)
        self.model = genai.GenerativeModel('gemini-2.0-flash')

    def generate_assignment(self, topic, credentials_file_path, question_details, pdf_file=None, duration=None, difficulty=None, learning_objectives=None, additional_requirements=None):
        try:
            gemini_prompt = f"""
            You are an expert educator. Generate a high-quality assignment in valid JSON format only.
            The output must strictly follow this structure:
            {{
                "topic": "{topic}",
                "questions": [
                    {{
                        "question_number": 1,
                        "question_text": "A clear, well-crafted question",
                        "answer": "A detailed, comprehensive answer",
                        "marks": 20
                    }}
                ]
            }}
            
            Topic: {topic}
            Number of questions: {len(question_details)}
            Difficulty: {difficulty}
            Learning Objectives: {learning_objectives}
            Additional Requirements: {additional_requirements}
            
            Important:
            - Generate exactly {len(question_details)} questions
            - Ensure valid JSON format
            - Only output the JSON, no additional text
            """
            
            if pdf_file:
                pdf_text = read_pdf(pdf_file)
                if pdf_text:
                    gemini_prompt += f"\nContext from PDF:\n{pdf_text}"

            response = self.model.generate_content(gemini_prompt)
            # Clean the response and ensure valid JSON
            json_str = re.sub(r'```json\s*|\s*```', '', response.text.strip())
            assignment_data = json.loads(json_str)

            return {
                'topic': topic,
                'content': assignment_data,
                'duration': duration,
                'output_type': 'json'
            }

        except json.JSONDecodeError as e:
            print(f"JSON parsing error: {e}")
            print(f"Raw response: {response.text}")
            return None
        except Exception as e:
            print(f"Error generating assignment: {e}")
            return None

    def create_pdf(self, assignment_data, include_answers=True):
        try:
            filename = "assignment.pdf" if include_answers else "questions.pdf"
            doc = SimpleDocTemplate(filename, pagesize=letter)
            styles = getSampleStyleSheet()
            content = []

            # Add title
            content.append(Paragraph(assignment_data['topic'], styles['Title']))
            if assignment_data.get('duration'):
                content.append(Paragraph(f"Duration: {assignment_data['duration']}", styles['Normal']))
                content.append(Spacer(1, 12))
            
            # Calculate and add total marks
            total_marks = sum(question.get('marks', 0) for question in assignment_data['content']['questions'])
            content.append(Paragraph(f"Total Marks: {total_marks}", styles['Normal']))
            content.append(Spacer(1, 12))

            # Add questions and optionally answers
            for question in assignment_data['content']['questions']:
                content.append(Paragraph(f"Question {question['question_number']}: {question['question_text']}", styles['Normal']))
                content.append(Spacer(1, 6))
                
                if include_answers:
                    content.append(Paragraph(f"Answer: {question['answer']}", styles['Normal']))
                
                content.append(Paragraph(f"Marks: {question['marks']}", styles['Normal']))
                content.append(Spacer(1, 12))

            doc.build(content)
            return filename

        except Exception as e:
            print(f"PDF creation error: {e}")
            return None
