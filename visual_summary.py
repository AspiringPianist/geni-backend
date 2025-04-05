import base64
import os
import mimetypes
from google import genai
from google.genai import types
from dotenv import load_dotenv
import json
import cloudinary.uploader
import random

load_dotenv()

# Configure Cloudinary
cloudinary.config(
    cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME"),
    api_key=os.getenv("CLOUDINARY_API_KEY"),
    api_secret=os.getenv("CLOUDINARY_API_SECRET"),
)

def save_binary_file(file_name, data):
    with open(file_name, "wb") as f:
        f.write(data)

def generate_image_prompt(client, section_content):
    """Use Gemini to generate a tailored image prompt"""
    prompt = f"""
    Create a detailed and creative prompt for an image generation model to produce an illustration that complements 
    the following textbook section content: "{section_content}". 
    The prompt should:
    - Encourage a vivid, artistic, or symbolic depiction (e.g., capturing mood, themes, or key moments),
    - Avoid directly replicating the text or including text in the image,
    - Be specific enough to inspire a unique visual that enhances the narrative,
    - Be concise (1-2 sentences).
    Return the prompt as a plain string, no additional formatting.
    """
    try:
        response = client.models.generate_content(
            model='gemini-2.0-flash-lite',
            contents=prompt,
        )
        return response.text.strip()
    except Exception as e:
        print(f"Error generating image prompt: {e}")
        # Fallback prompt if generation fails
        return f"Create a vivid illustration capturing the mood and themes of '{section_content}' without replicating the text."

def generate_image(section_content):
    print(f"Generating image for section content: {section_content}")
    client = genai.Client(api_key=os.getenv("GEMINI_API"))

    # Generate the tailored prompt using Gemini
    image_prompt = generate_image_prompt(client, section_content)
    print(f"Generated image prompt: {image_prompt}")

    model = "gemini-2.0-flash-exp-image-generation"
    contents = [
        types.Content(
            role="user",
            parts=[
                types.Part.from_text(text=image_prompt),
            ],
        ),
    ]
    generate_content_config = types.GenerateContentConfig(
        temperature=1,
        top_p=0.95,
        top_k=40,
        max_output_tokens=8192,
        response_modalities=["image", "text"],
    )

    try:
        for chunk in client.models.generate_content_stream(
            model=model,
            contents=contents,
            config=generate_content_config,
        ):
            if not chunk.candidates or not chunk.candidates[0].content or not chunk.candidates[0].content.parts:
                continue
            if chunk.candidates[0].content.parts[0].inline_data:
                inline_data = chunk.candidates[0].content.parts[0].inline_data
                file_extension = mimetypes.guess_extension(inline_data.mime_type)
                file_name = f"generated_image_{random.randint(1000, 9999)}{file_extension}"
                save_binary_file(file_name, inline_data.data)
                print(f"Image generated and saved as {file_name}")
                return file_name
            else:
                print(f"Text response (no image): {chunk.text}")
        print("No image generated.")
        return None
    except Exception as e:
        print(f"Error generating image: {e}")
        return None

def upload_to_cloudinary(image_path):
    print(f"Uploading image {image_path} to Cloudinary")
    try:
        response = cloudinary.uploader.upload(image_path)
        print(f"Image uploaded to Cloudinary: {response['secure_url']}")
        return response["secure_url"]
    except Exception as e:
        print(f"Error uploading to Cloudinary: {e}")
        return None

def generate_visual_summary_json(topic, rag):
    print('Received:', topic, rag)
    load_dotenv()
    client = genai.Client(api_key=os.getenv("GEMINI_API"))
    prompt = f"""
    Generate a Visual Summary in JSON format for the topic "{topic}". The summary should be divided into 3-5 sections, 
    each representing a key event or era. For each section, include:
    - A "title" (short, descriptive heading),
    - A "text" field (2-3 sentences summarizing the event/era),
    - Placeholder fields for "imageUrl" and "audioUrl" (set as empty strings for now).
    {rag}
    Ensure the content is engaging, concise, and suitable for an immersive, story-like presentation with visuals and audio narration. 
    The JSON should follow this schema:
    {{
        "type": "summary",
        "title": "Visual Summary Title",
        "sections": [
            {{
                "title": "Section Title",
                "text": "Section summary text",
                "imageUrl": "",
                "audioUrl": ""
            }}
        ]
    }}
    """
    try:
        response = client.models.generate_content(
            model='gemini-2.0-flash-lite',
            contents=prompt,
        )
        import re
        json_string = re.sub(r'```json\s*([\s\S]*?)\s*```', r'\1', response.text).strip()
        visual_summary = json.loads(json_string)
        # visual_summary['type'] = 'summary'
    except json.JSONDecodeError as e:
        print(f"Error decoding JSON: {e}")
        return None
    except Exception as e:
        print(f"Error: {e}")
        visual_summary = {"title": f"Error generating visual summary for {topic}", "sections": []}

    print("Generated visual summary:", visual_summary)
    
    for i, section in enumerate(visual_summary.get("sections", [])):
        image_path = generate_image(section["text"])
        print(f'Image Path for Section {i+1}: {image_path}')
        if image_path:
            image_url = upload_to_cloudinary(image_path)
            print(f'Image URL for Section {i+1}: {image_url}')
            if image_url:
                section["imageUrl"] = image_url
            try:
                os.remove(image_path)
                print(f"Local image file {image_path} removed")
            except OSError as e:
                print(f"Error removing file {image_path}: {e}")

    with open('visual_summary.json', 'w') as f:
        json.dump(visual_summary, f, indent=4)
    return visual_summary

if __name__ == "__main__":
    generate_visual_summary_json(
        "World War II",
        """```rag
        R: The war began in 1939 and ended in 1945.
        A: The war involved major world powers and resulted in significant loss of life.
        G: The war led to the establishment of the United Nations and shaped global politics.
        ```"""
    )