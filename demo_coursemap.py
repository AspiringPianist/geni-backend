import google.generativeai as genai
from typing import List, Dict
from fpdf import FPDF
import os
from dotenv import load_dotenv
import tempfile
import random
import json
import re

def return_json(responseText):
    json_string = re.sub(r'```json\s*([\s\S]*?)\s*```', r'\1', responseText).strip()
    json_string = json_string.strip()
    return json.loads(json_string)

class customPDF(FPDF):
    def __init__(self, orientation='P', unit='mm', format='A4', course_title="Course Curriculum", author=""):
        super().__init__(orientation, unit, format)
        self.course_title = course_title
        self.author = author
        self.set_auto_page_break(auto=True, margin=15)
        self.set_margins(left=20, top=20, right=20)
        # Add custom fonts (if needed)
        self.add_page()
        self.add_cover_page()
        #self.add_page()
        #self.toc_page = self.page_no()

    @staticmethod
    def clean_text(text: str) -> str:
        """Clean text by removing asterisks and non-ASCII characters"""
        # Remove asterisks
        text = text.replace('*', '')
        # Remove non-ASCII characters
        text = ''.join(char for char in text if ord(char) < 128)
        return text.strip()

    # Then in the content_text method:
    def content_text(self, text):
        """Add regular content text"""
        self.set_font('Arial', '', 11)
        clean_text = self.clean_text(text)
        self.multi_cell(0, 6, clean_text)
        self.ln(4)
        
    def add_cover_page(self):
        # Cover page
        self.set_font('Arial', 'B', 30)
        self.set_text_color(44, 62, 80)  # Dark blue-gray
        self.cell(0, 60, '', 0, 1, 'C')  # Add some space at the top
        self.cell(0, 20, self.course_title, 0, 1, 'C')
        
        if self.author:
            self.set_font('Arial', 'I', 14)
            self.set_text_color(52, 73, 94)  # Slightly lighter blue-gray
            self.cell(0, 15, f"Presented by: {self.author}", 0, 1, 'C')
        
        # Add date
        self.set_font('Arial', '', 12)
        from datetime import date
        today = date.today()
        self.cell(0, 10, today.strftime("%B %Y"), 0, 1, 'C')
        
        # Add a decorative element
        self.set_draw_color(52, 152, 219)  # Blue
        self.set_line_width(1)
        self.line(40, 160, 170, 160)

    def header(self):
        # Skip header on first page (cover page)
        if self.page_no() == 1:
            return
            
        # Add logo if available
        # self.image('logo.png', 10, 8, 33)
        
        # Header with line
        self.set_font('Arial', 'B', 12)
        self.set_text_color(44, 62, 80)  # Dark blue-gray
        self.cell(0, 10, self.course_title, 0, 0, 'R')
        self.set_draw_color(52, 152, 219)  # Blue
        self.set_line_width(0.5)
        self.line(20, 20, 190, 20)
        self.ln(15)

    def footer(self):
        # Skip footer on first page (cover page)
        if self.page_no() == 1:
            return
            
        self.set_y(-15)
        self.set_font('Arial', 'I', 8)
        self.set_text_color(127, 140, 141)  # Gray
        self.cell(0, 10, f'Page {self.page_no()-1}', 0, 0, 'C')
        
        # Add a thin line above the footer
        self.set_draw_color(189, 195, 199)  # Light gray
        self.set_line_width(0.3)
        self.line(20, 282, 190, 282)

    

    def module_title(self, title):
        """Add a module title (main chapter)"""
        #self.add_toc_entry(title, 1)
        self.set_font('Arial', 'B', 18)
        
        # Create a background rectangle
        self.set_fill_color(52, 152, 219)  # Blue
        self.set_draw_color(41, 128, 185)  # Darker blue for border
        self.set_text_color(255, 255, 255)  # White text
        self.rect(20, self.get_y(), 170, 12, 'DF')
        
        # Add module number or icon if desired
        self.cell(0, 12, f" {title}", 0, 1, 'L')
        self.set_text_color(0, 0, 0)  # Reset text color
        self.ln(8)

    def section_title(self, title):
        """Add a section title"""
        clean_title = self.clean_text(title)

        #self.add_toc_entry(clean_title, 2)
        self.set_font('Arial', 'B', 14)
        self.set_text_color(52, 152, 219)  # Blue
        self.cell(0, 10, clean_title, 0, 1, 'L')
        
        # Add underline
        self.set_draw_color(52, 152, 219)  # Blue
        self.set_line_width(0.3)
        self.line(20, self.get_y(), 120, self.get_y())
        
        self.set_text_color(0, 0, 0)  # Reset text color
        self.ln(5)

    def subsection_title(self, title):
        """Add a subsection title"""
       # self.add_toc_entry(title, 3)
        self.set_font('Arial', 'BI', 12)
        self.set_text_color(52, 73, 94)  # Blue-gray
        self.cell(0, 8, title, 0, 1, 'L')
        self.set_text_color(0, 0, 0)  # Reset text color
        self.ln(2)

    def content_text(self, text):
        """Add regular content text"""
        self.set_font('Arial', '', 11)
        # Clean text to remove non-ASCII characters
        clean_text = ''.join(char for char in text if ord(char) < 128)
        self.multi_cell(0, 6, clean_text)
        self.ln(4)

    def bullet_point(self, text):
        """Add a bullet point"""
        self.set_font('Arial', '', 11)
        # Save the current position
        x = self.get_x()
        y = self.get_y()
        
        # Add bullet
        self.set_font('ZapfDingbats', '', 10)
        self.cell(6, 6, chr(108), 0, 0)  # ZapfDingbats bullet character
        
        # Add text
        self.set_font('Arial', '', 11)
        self.set_x(x + 8)
        clean_text = ''.join(char for char in text if ord(char) < 128)
        self.multi_cell(0, 6, clean_text)
        
        self.ln(2)

    def add_weekly_modules(self, modules):
        """Add weekly modules with formatted content"""
        self.set_font('Arial', 'B', 14)
        self.set_text_color(52, 152, 219)
        self.cell(0, 10, "Weekly Modules", 0, 1, 'L')
        
        self.ln(5)
        for module in modules:
            # Week title
            self.set_font('Arial', 'B', 12)
            self.set_text_color(44, 62, 80)
            week_title = f"Week {module['week']}: {module['title']}"
            self.cell(0, 8, week_title, 0, 1, 'L')
            
            # Topics
            self.set_font('Arial', '', 11)
            for topic in module['topics']:
                self.set_x(25)
                self.set_font('ZapfDingbats', '', 10)
                self.cell(6, 6, chr(108), 0, 0)  # Bullet point
                self.set_font('Arial', '', 11)
                self.set_x(33)
                clean_topic = ''.join(char for char in topic if ord(char) < 128)
                self.multi_cell(0, 6, clean_topic)
            
            # Activity
            if module['activity']:
                self.ln(2)
                self.set_x(25)
                self.set_font('Arial', 'B', 11)
                self.cell(0, 6, "Practical Activity:", 0, 1)
                self.set_x(25)
                self.set_font('Arial', '', 11)
                clean_activity = ''.join(char for char in module['activity'] if ord(char) < 128)
                self.multi_cell(0, 6, clean_activity)
            
            self.ln(10)
        
        self.set_text_color(0, 0, 0)  # Reset text color


    def add_timeline(self, weeks):
        """Add a visual timeline for the course"""
        self.ln(5)
        
        # Title
        self.set_font('Arial', 'B', 14)
        self.set_text_color(52, 152, 219)
        self.cell(0, 10, "Course Timeline", 0, 1, 'L')
        self.ln(2)
        
        # Timeline
        total_width = 170
        box_width = total_width / weeks
        
        for week in range(1, weeks + 1):
            # Calculate positions
            x_pos = 20 + (week - 1) * box_width
            
            # Draw box
            self.set_fill_color(245, 245, 245)
            self.set_draw_color(52, 152, 219)
            self.rect(x_pos, self.get_y(), box_width - 2, 12, 'DF')
            
            # Add week number
            self.set_font('Arial', 'B', 9)
            self.set_text_color(52, 73, 94)
            self.set_xy(x_pos, self.get_y())
            self.cell(box_width - 2, 12, f"Week {week}", 0, 0, 'C')
        
        self.ln(20)
        self.set_text_color(0, 0, 0)  # Reset text color
        
    def add_learning_objectives(self, objectives):
        """Add learning objectives with clean formatting"""
        self.set_font('Arial', 'B', 14)
        self.set_text_color(52, 152, 219)
        self.cell(0, 10, "Learning Objectives", 0, 1, 'L')
        
        # Add objectives
        self.set_text_color(44, 62, 80)
        self.ln(5)
        
        for objective in objectives:
            self.set_x(25)
            self.set_font('ZapfDingbats', '', 10)
            self.cell(6, 6, chr(110), 0, 0) 
            self.set_font('Arial', '', 11)
            self.set_x(33)
            clean_text = ''.join(char for char in objective if ord(char) < 128)
            self.cell(0, 6, clean_text, 0, 1)
            self.ln(5)
        
        self.set_text_color(0, 0, 0)
    
    def add_resources_box(self, title, resources):
        """Add resources with formatted titles"""
        self.set_font('Arial', 'B', 12)
        self.set_text_color(0,0,0)
        self.cell(0, 8, title, 0, 1, 'L')
        
        self.ln(2)
        for resource in resources:
            self.set_x(25)
            clean_text = ''.join(char for char in resource if ord(char) < 128)
            clean_text = clean_text.replace('**', '')  # Remove asterisks
            
            # Check if it's a book (contains "by" or "edition")
            if "by" in clean_text.lower() or "edition" in clean_text.lower():
                self.set_font('Arial', 'B', 10)
                book_title = clean_text.split("by")[0].strip() if "by" in clean_text else clean_text.split("edition")[0].strip()
                self.multi_cell(160, 6, book_title)
                
                self.set_font('Arial', '', 10)
                remaining_text = clean_text[len(book_title):].strip()
                if remaining_text:
                    self.set_x(25)
                    self.multi_cell(160, 6, remaining_text)
            else:
                self.set_font('Arial', '', 10)
                self.multi_cell(160, 6, clean_text)
                
            self.ln(5)
        
        self.set_text_color(0, 0, 0)  # Reset text color

        
    def add_assessment_table(self, assessments):
        """Add a table for assessments with percentages"""
        self.set_font('Arial', 'B', 14)
        self.set_text_color(52, 152, 219)
        self.cell(0, 10, "Assessment Structure", 0, 1, 'L')
        self.ln(2)
        
        # Column headers
        self.set_font('Arial', 'B', 11)
        self.set_fill_color(52, 152, 219)
        self.set_text_color(255, 255, 255)
        self.cell(100, 8, "Assessment", 1, 0, 'C', True)
        self.cell(30, 8, "Weight", 1, 0, 'C', True)
        self.cell(40, 8, "Due Date", 1, 1, 'C', True)
        
        # Assessment rows
        self.set_font('Arial', '', 10)
        self.set_text_color(44, 62, 80)
        
        fill = False
        for assessment in assessments:
            name = assessment.get('name', '')
            weight = assessment.get('weight', '')
            due = assessment.get('due', '')
            
            # Alternate row colors
            if fill:
                self.set_fill_color(240, 248, 255)  # Light blue
            else:
                self.set_fill_color(255, 255, 255)  # White
                
            clean_name = ''.join(char for char in name if ord(char) < 128)
            self.cell(100, 7, clean_name, 1, 0, 'L', fill)
            self.cell(30, 7, weight, 1, 0, 'C', fill)
            self.cell(40, 7, due, 1, 1, 'C', fill)
            
            fill = not fill
            
        self.ln(10)

class CourseGenerator:
    def __init__(self):
        load_dotenv()
        api_key = os.getenv('GEMINI_API')
        if not api_key:
            raise ValueError("GEMINI_API key not found in environment variables")
        genai.configure(api_key=api_key)
        self.model = genai.GenerativeModel('gemini-2.0-flash')

    def _generate_json_content(self, prompt: str) -> dict:
        """Helper method to generate and parse JSON content from the model"""
        # Ensure the prompt explicitly requests JSON
        json_prompt = f"{prompt}\n\nReturn the response only in JSON format."
        try:
            response = self.model.generate_content(json_prompt)
            print(response)
            return return_json(response.text)
        except json.JSONDecodeError as e:
            print(f"JSON parsing error: {e}")
            return {}
        except Exception as e:
            print(f"Error generating content: {e}")
            return {}

    def generate_learning_outcomes(self, course_details: dict) -> List[str]:
        prompt = f"""Generate 5-6 concise learning outcomes for {course_details['topic']} course.
        Each outcome should:
        - Start with an action verb (e.g., analyze, evaluate, design)
        - Be measurable and specific
        - Align with {course_details['difficulty']} level
        - Reflect skills relevant to the industry
        Format: Return as a JSON array of strings, like this:
        ["Outcome 1", "Outcome 2", "Outcome 3", "Outcome 4", "Outcome 5", "Outcome 6"]
        """
        result = self._generate_json_content(prompt)
        return result if isinstance(result, list) else []

    def generate_weekly_modules(self, course_details: dict) -> List[Dict[str, str]]:
        weeks = int(course_details['duration']) * 4
        prompt = f"""Create a detailed {weeks}-week curriculum for {course_details['topic']}.
        Level: {course_details['difficulty']}

        For each week include:
        - Week X: [Specific Theme/Topic]
        - Topics: 3 specific topics
        - Practical Activity: [Detailed hands-on exercise or project work]

        Make content progressive and interconnected across weeks.
        Keep each topic and activity specific to {course_details['topic']}.
        
        Format the response as a JSON array of objects:
        [
            {{
                "week": "1",
                "title": "Week Title",
                "topics": ["Topic 1", "Topic 2", "Topic 3"],
                "activity": "Practical Activity Description"
            }},
            ...
        ]
        """
        result = self._generate_json_content(prompt)
        return result[:weeks] if isinstance(result, list) else []

    def generate_course_overview(self, course_details: dict) -> str:
        prompt = f"""Write a professional course overview for {course_details['topic']}.
        Level: {course_details['difficulty']}
        Mode: {course_details['teaching_mode']}
        Duration: {course_details['duration']} months
        
        Include:
        - Brief introduction to the subject matter
        - Importance and relevance in today’s context
        - Target audience and prerequisites
        - Brief mention of teaching approach
        
        Keep it concise (150-200 words) and professionally worded.
        Format the response as a JSON object with a single key 'overview':
        {{ "overview": "Course overview text here" }}
        """
        result = self._generate_json_content(prompt)
        return result.get("overview", "") if isinstance(result, dict) else ""

    def generate_detailed_content(self, course_details: dict) -> dict:
        weeks = int(course_details['duration']) * 4
        prompt = f"""Create curriculum content for {course_details['topic']}.
        Level: {course_details['difficulty']}

        Include:
        - 5-6 Learning Outcomes
        - Weekly content for {weeks} weeks, each with:
          - Title
          - 3 Topics
          - 1 Practical Activity

        Make content specific and progressive.
        Format as a JSON object:
        {{
            "outcomes": ["Outcome 1", "Outcome 2", ...],
            "modules": [
                {{
                    "week": "1",
                    "title": "Week Title",
                    "topics": ["Topic 1", "Topic 2", "Topic 3"],
                    "activity": "Practical Activity Description"
                }},
                ...
            ]
        }}
        """
        result = self._generate_json_content(prompt)
        return result if isinstance(result, dict) else {"outcomes": [], "modules": []}

    def generate_assessment_structure(self, course_details: dict) -> List[Dict[str, str]]:
        assignments = int(course_details['assignments'])
        prompt = f"""Generate {assignments} detailed assessment components for {course_details['topic']} course.
        Assessment Type: {course_details['assessment_type']}
        Level: {course_details['difficulty']}

        For each assessment provide:
        - Title
        - Weight (ensure total is 100%)
        - Description
        - Due (specific week/month)

        Format as a JSON array of objects:
        [
            {{
                "name": "Assessment Title",
                "weight": "X%",
                "description": "Description text",
                "due": "Week X"
            }},
            ...
        ]
        """
        result = self._generate_json_content(prompt)
        assessments = result[:assignments] if isinstance(result, list) else []
        
        # Ensure weights total 100%
        total_weight = sum(int(a.get("weight", "0").replace("%", "")) for a in assessments)
        if total_weight != 100 and assessments:
            for i, a in enumerate(assessments):
                a["weight"] = f"{int(100/len(assessments)) if i < len(assessments)-1 else 100 - int(100/len(assessments))*(len(assessments)-1)}%"

        return assessments

    def generate_resources(self, course_details: dict) -> List[str]:
        prompt = f"""List 5-7 recommended resources for {course_details['topic']} course at {course_details['difficulty']} level.
        Include a mix of:
        - Books (with authors)
        - Online courses/platforms
        - Tools/software
        - Websites/documentation
        
        Format as a JSON array of strings:
        ["Resource 1", "Resource 2", ...]
        """
        result = self._generate_json_content(prompt)
        return result[:7] if isinstance(result, list) else []

    def format_pdf(self, details, outcomes, overview, modules, assessments, resources) -> str:
        # [Existing format_pdf method remains largely unchanged]
        pdf = customPDF(course_title=details['topic'], author=details.get('instructor', 'Course Creator'))
        
        pdf.add_page()
        pdf.module_title("Course Overview")
        pdf.content_text(overview)
        
        pdf.add_page()
        pdf.module_title("Course Details")
        pdf.bullet_point(f"Level: {details['difficulty']}")
        pdf.bullet_point(f"Duration: {details['duration']} months")
        pdf.bullet_point(f"Teaching Mode: {details['teaching_mode']}")
        pdf.bullet_point(f"Assessment Type: {details['assessment_type']}")
        
        pdf.add_page()
        pdf.add_learning_objectives(outcomes)
        
        pdf.add_page()
        pdf.module_title("Weekly Modules")
        for module in modules:
            week_title = f"Week {module['week']}: {module['title']}"
            pdf.section_title(week_title)
            for topic in module['topics']:
                pdf.bullet_point(topic)
            if module.get('activity'):
                pdf.subsection_title("Activity")
                pdf.content_text(module['activity'])
        
        pdf.add_page()
        assessments_formatted = [{"name": a['name'], "weight": a['weight'], "due": a['due']} for a in assessments]
        pdf.add_assessment_table(assessments_formatted)
        
        pdf.add_page()
        pdf.module_title("Assessment Details")
        for assessment in assessments:
            pdf.section_title(assessment['name'])
            pdf.content_text(assessment.get('description', ''))
        
        pdf.add_page()
        pdf.add_resources_box("Recommended Resources", resources)
        
        output_file = tempfile.NamedTemporaryFile(suffix='.pdf', delete=False).name
        pdf.output(output_file, 'F')
        return output_file

    def generate_curriculum(self, course_details: dict) -> str:
        detailed_content = self.generate_detailed_content(course_details)
        course_overview = self.generate_course_overview(course_details)
        assessments = self.generate_assessment_structure(course_details)
        resources = self.generate_resources(course_details)
        
        return self.format_pdf(
            course_details,
            detailed_content.get("outcomes", []),
            course_overview,
            detailed_content.get("modules", []),
            assessments,
            resources
        )

# Example usage
if __name__ == "__main__":
    course_details = {
        "topic": "Game Dev in Unity and Python",
        "difficulty": "Intermediate",
        "duration": "3",
        "teaching_mode": "Online",
        "assessment_type": "Project-based",
        "assignments": "4",
        "instructor": "Dr. Snehal Sharma and Prof. Siddharth Kini"
    }
    generator = CourseGenerator()
    pdf_path = generator.generate_curriculum(course_details)
    print(f"Generated PDF at: {pdf_path}")