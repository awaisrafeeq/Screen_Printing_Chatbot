# pdf_extractor.py - IMPROVED VERSION
import fitz  # PyMuPDF
import re

def extract_pdf_text(pdf_path: str) -> str:
    """Extracts text from the PDF located at the given path."""
    try:
        doc = fitz.open(pdf_path)
        text = ""
        for page in doc:
            text += page.get_text()
        doc.close()
        return text
    except Exception as e:
        print(f"Error extracting PDF: {e}")
        return ""

def extract_faq_data(pdf_text: str) -> dict:
    """Extracts FAQ data into a structured dictionary."""
    faqs = {}
    
    # Pattern to match FAQ items (number followed by parenthesis)
    # Matches patterns like: "1) Question? Answer"
    pattern = r'(\d+)\)\s*([^?]+\?)\s*([^\n]+(?:\n(?!\d+\))[^\n]+)*)'
    
    matches = re.findall(pattern, pdf_text, re.MULTILINE)
    
    for match in matches:
        number = match[0]
        question = match[1].strip()
        answer = match[2].strip()
        
        # Clean up the answer - remove extra whitespace and newlines
        answer = ' '.join(answer.split())
        
        # Store with lowercase question as key for easy matching
        faqs[question.lower()] = answer
    
    # If regex doesn't work well, fallback to line-by-line parsing
    if not faqs:
        lines = pdf_text.split('\n')
        current_question = None
        current_answer = []
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
                
            # Check if line starts with number and parenthesis
            if re.match(r'^\d+\)', line):
                # Save previous Q&A if exists
                if current_question and current_answer:
                    faqs[current_question.lower()] = ' '.join(current_answer)
                
                # Split on first question mark to separate question from answer
                parts = line.split('?', 1)
                if len(parts) == 2:
                    # Remove the number prefix
                    question_part = re.sub(r'^\d+\)\s*', '', parts[0]).strip()
                    current_question = question_part + '?'
                    current_answer = [parts[1].strip()] if parts[1].strip() else []
                else:
                    # Question might be on this line, answer on next
                    current_question = re.sub(r'^\d+\)\s*', '', line).strip()
                    current_answer = []
            elif current_question and not re.match(r'^\d+\)', line):
                # This is part of the current answer
                current_answer.append(line)
        
        # Don't forget the last Q&A
        if current_question and current_answer:
            faqs[current_question.lower()] = ' '.join(current_answer)
    
    print(f"Extracted {len(faqs)} FAQ items")
    
    # Print first few for debugging
    if faqs:
        items = list(faqs.items())[:3]
        for q, a in items:
            print(f"Sample Q: {q[:50]}...")
            print(f"Sample A: {a[:100]}...")
    
    return faqs