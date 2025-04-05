from google import genai
import json
from dotenv import load_dotenv
import os
load_dotenv()

def generate_response(prompt):
    client = genai.Client(api_key=os.getenv("GEMINI_API"))
    response = client.models.generate_content(
        model="gemini-2.0-flash-lite",
        contents=prompt,
    )

    return response
