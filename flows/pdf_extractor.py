# pdf_extractor.py - FLEXIBLE VERSION FOR TESTING
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
        print(f"Raw PDF text (first 500 chars): {repr(text[:500])}...")  # Debug with repr for special chars
        print(f"Full PDF text length: {len(text)} chars")
        return text
    except Exception as e:
        print(f"Error extracting PDF: {e}")
        return ""

def extract_faq_data(pdf_text: str) -> dict:
    """Extracts FAQ data into a structured dictionary - more flexible for testing."""
    faqs = {}
    
    # Log raw input for debugging
    print(f"Processing PDF text (length: {len(pdf_text)} chars)")
    
    # More flexible pattern: Matches numbered items like "1. Question Answer" or "1) Question? Answer"
    # Allows questions without ? and captures multi-line answers
    pattern = r'(\d+[.)])\s*([^.?]+(?:\?|\.))?\s*:?\s*([^\d\n]+(?:\n(?!\d+[.)])[^\d\n]+)*)'
    
    matches = re.findall(pattern, pdf_text, re.MULTILINE | re.IGNORECASE)
    print(f"Regex found {len(matches)} potential matches")
    
    for match in matches:
        number, question, answer = match
        question = (question or "").strip()
        answer = ' '.join(answer.strip().split())  # Clean whitespace
        
        # Make question end with ? if it doesn't
        if question and not question.endswith('?'):
            question += '?'
        
        # Skip if too short or invalid
        if len(question) > 5 and len(answer) > 5:
            faqs[question.lower()] = answer
            print(f"Added FAQ: Q={question[:50]}... A={answer[:50]}...")
        else:
            print(f"Skipped short FAQ: Q={question[:50]}... A={answer[:50]}...")
    
    # Improved fallback: Line-by-line parsing (handles more formats)
    if len(faqs) < 3:  # Only fallback if regex gets few/no matches
        print("Using fallback line-by-line parsing...")
        lines = [line.strip() for line in pdf_text.split('\n') if line.strip()]
        current_question = None
        current_answer = []
        
        for line in lines:
            # Start new FAQ if line looks like numbered question
            if re.match(r'^\d+[.)]', line):
                # Save previous
                if current_question and current_answer:
                    answer_text = ' '.join(current_answer).strip()
                    if len(current_question) > 5 and len(answer_text) > 5:
                        q_key = current_question.lower().rstrip('?') + '?'
                        faqs[q_key] = answer_text
                        print(f"Fallback added: Q={q_key[:50]}... A={answer_text[:50]}...")
                
                # New question
                line = re.sub(r'^\d+[.)]\s*', '', line).strip()
                if ':' in line:
                    parts = line.split(':', 1)
                    current_question = parts[0].strip() + '?'
                    current_answer = [parts[1].strip()] if len(parts) > 1 else []
                else:
                    current_question = line + '?'
                    current_answer = []
            elif current_question and current_answer:  # Add to answer
                current_answer.append(line)
            elif not current_question and re.search(r'\?', line):  # Loose question match
                current_question = line + '?'
                current_answer = []
        
        # Save last one
        if current_question and current_answer:
            answer_text = ' '.join(current_answer).strip()
            if len(current_question) > 5 and len(answer_text) > 5:
                q_key = current_question.lower().rstrip('?') + '?'
                faqs[q_key] = answer_text
                print(f"Fallback added last: Q={q_key[:50]}... A={answer_text[:50]}...")
    
    # Final count and samples
    print(f"Total extracted {len(faqs)} FAQ items")
    if faqs:
        items = list(faqs.items())[:3]
        for q, a in items:
            print(f"Sample Q: {q[:50]}...")
            print(f"Sample A: {a[:100]}...")
    else:
        print("❌ No FAQs extracted - check PDF format (should be like '1. How much? Answer here.')")
    
    return faqs
