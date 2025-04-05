import os
from google import genai
from dotenv import load_dotenv
import json

load_dotenv()

def generate_quiz_json(topic, rag=""):
    print(f"Generating quiz for topic: {topic}, rag: {rag}")
    client = genai.Client(api_key=os.getenv("GEMINI_API"))
    prompt = f"""
    Generate a Quiz in JSON format for the topic "{topic}". The quiz should include 3-5 questions, each with:
    - A "question" (clear and concise),
    - An "options" array (4 possible answers labeled A, B, C, D),
    - A "correctAnswer" (the correct option letter: A, B, C, or D),
    - A "difficulty" (easy, medium, or hard).
    {rag}
    Ensure the questions are engaging, relevant to the topic, and suitable for a learning aid. 
    Return the response only in JSON format with this schema:
    {{
        "type": "quiz",
        "title": "Quiz Title",
        "questions": [
            {{
                "question": "Question text",
                "options": ["Option A", "Option B", "Option C", "Option D"],
                "correctAnswer": "A",
                "difficulty": "easy"
            }}
        ],
        "latestScore": null  // Initially None, updated after attempts
    }}
    """
    try:
        response = client.models.generate_content(
            model='gemini-2.0-flash-lite',
            contents=prompt,
        )
        import re
        json_string = re.sub(r'```json\s*([\s\S]*?)\s*```', r'\1', response.text).strip()
        quiz_json = json.loads(json_string)
        return quiz_json
    except json.JSONDecodeError as e:
        print(f"Error decoding JSON: {e}")
        return {"title": f"Quiz on {topic}", "questions": [], "latestScore": "null"}
    except Exception as e:
        print(f"Error generating quiz: {e}")
        return {"title": f"Quiz on {topic}", "questions": [], "latestScore": "null"}