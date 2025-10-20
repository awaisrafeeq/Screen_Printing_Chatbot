# pdf_extractor.py - ENHANCED VERSION FOR FULL ANSWERS
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
        print(f"Raw PDF text (first 500 chars): {repr(text[:500])}...")  # Debug with repr
        print(f"Full PDF text length: {len(text)} chars")
        return text
    except Exception as e:
        print(f"Error extracting PDF: {e}")
        return ""

def extract_faq_data(pdf_text: str) -> dict:
    """Extracts FAQ data into a structured dictionary with improved multi-line support."""
    faqs = {}
    
    # Log raw input for debugging
    print(f"Processing PDF text (length: {len(pdf_text)} chars)")
    
    # Enhanced pattern: Matches numbered items and captures multi-line answers until next number
    pattern = r'(\d+[.)])\s*([^.?]+(?:\?|\.))?\s*:?\s*(.+?)(?=(?:\d+[.)]|\Z))'
    
    matches = re.findall(pattern, pdf_text, re.DOTALL | re.MULTILINE)
    print(f"Regex found {len(matches)} potential matches")
    
    for match in matches:
        number, question, answer = match
        question = (question or "").strip()
        answer = ' '.join(answer.strip().split())  # Clean whitespace
        
        # Ensure question ends with ?
        if question and not question.endswith('?'):
            question += '?'
        
        # Skip if too short
        if len(question) > 5 and len(answer) > 5:
            faqs[question.lower()] = answer
            print(f"Added FAQ: Q={question[:50]}... A={answer[:100]}...")
        else:
            print(f"Skipped short FAQ: Q={question[:50]}... A={answer[:50]}...")
    
    # Fallback: Line-by-line with better multi-line answer collection
    if len(faqs) < 3:
        print("Using fallback line-by-line parsing...")
        lines = [line.strip() for line in pdf_text.split('\n') if line.strip()]
        current_question = None
        current_answer = []
        collecting_answer = False
        
        for line in lines:
            if re.match(r'^\d+[.)]', line):
                if current_question and current_answer:
                    answer_text = ' '.join(current_answer).strip()
                    if len(current_question) > 5 and len(answer_text) > 5:
                        q_key = current_question.lower().rstrip('?') + '?'
                        faqs[q_key] = answer_text
                        print(f"Fallback added: Q={q_key[:50]}... A={answer_text[:100]}...")
                # Start new FAQ
                line = re.sub(r'^\d+[.)]\s*', '', line).strip()
                if ':' in line:
                    parts = line.split(':', 1)
                    current_question = parts[0].strip() + '?'
                    current_answer = [parts[1].strip()] if len(parts) > 1 else []
                else:
                    current_question = line + '?'
                    current_answer = []
                collecting_answer = True
            elif collecting_answer and current_question:
                current_answer.append(line)
            elif re.search(r'\?', line) and not current_question:  # Loose question start
                current_question = line + '?'
                current_answer = []
                collecting_answer = True
        
        if current_question and current_answer:
            answer_text = ' '.join(current_answer).strip()
            if len(current_question) > 5 and len(answer_text) > 5:
                q_key = current_question.lower().rstrip('?') + '?'
                faqs[q_key] = answer_text
                print(f"Fallback added last: Q={q_key[:50]}... A={answer_text[:100]}...")
    
    print(f"Total extracted {len(faqs)} FAQ items")
    if faqs:
        items = list(faqs.items())[:3]
        for q, a in items:
            print(f"Sample Q: {q[:50]}...")
            print(f"Sample A: {a[:100]}...")
    else:
        print("❌ No FAQs extracted - check PDF format (e.g., '1. How much? Full answer here.')")
    
    return faqs
