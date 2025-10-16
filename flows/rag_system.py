# rag_system.py - FIXED VERSION
import faiss
import numpy as np
from sentence_transformers import SentenceTransformer
from flows.pdf_extractor import extract_faq_data, extract_pdf_text
import requests
import os
import time
from datetime import datetime, timedelta
# Load the Sentence-BERT model for embedding FAQ questions and answers
model = SentenceTransformer('all-MiniLM-L6-v2')

# Global variables for FAQ data and last update time
faq_data = None
faiss_index = None
faq_questions = None
faq_answers = None
last_update_time = 0

def load_faq_data():
    """Load FAQ data from PDF with weekly cache"""
    global faq_data, faiss_index, faq_questions, faq_answers, last_update_time
    
    # Check if we need to reload (once per week)
    current_time = time.time()
    one_week_seconds = 3 * 60  # 604800 seconds
    
    if current_time - last_update_time > one_week_seconds:
        print("🔄 Reloading FAQ data from PDF...")
        pdf_text = extract_pdf_text(r"https://raw.githubusercontent.com/awaisrafeeq/Screen_Printing_Chatbot/master/FAQ%20For%20quotes.pdf")
        faq_data = extract_faq_data(pdf_text)
        faiss_index, faq_questions, faq_answers, _ = create_faq_embeddings(faq_data)
        last_update_time = current_time
        print(f"✅ FAQ data reloaded with {len(faq_data)} items")
    else:
        print("📚 Using cached FAQ data")
# Extract FAQ data from PDF (replace with your actual file path)
# pdf_text = extract_pdf_text(r"https://github.com/awaisrafeeq/Screen_Printing_Chatbot/blob/master/FAQ%20For%20quotes.pdf")
# pdf_text = extract_pdf_text(r"https://raw.githubusercontent.com/awaisrafeeq/Screen_Printing_Chatbot/master/FAQ%20For%20quotes.pdf")
# url = "https://raw.githubusercontent.com/awaisrafeeq/Screen_Printing_Chatbot/master/FAQ%20For%20quotes.pdf"
# local_path = "FAQ.pdf"

# r = requests.get(url)
# with open(local_path, "wb") as f:
#     f.write(r.content)

# pdf_text = extract_pdf_text(local_path)

# faq_data = extract_faq_data(pdf_text)

# Create embeddings for FAQ questions only (not combining with answers)
def create_faq_embeddings(faq_data: dict):
    """Creates embeddings for FAQ questions and indexes them using FAISS."""
    faq_questions = list(faq_data.keys())
    faq_answers = list(faq_data.values())
    
    # Create embeddings for questions only
    faq_embeddings = model.encode(faq_questions, convert_to_numpy=True)
    
    # Create FAISS index
    dimension = faq_embeddings.shape[1]  # Dimensionality of the embeddings
    faiss_index = faiss.IndexFlatL2(dimension)  # Using L2 distance for similarity
    faiss_index.add(np.array(faq_embeddings, dtype=np.float32))  # Adding embeddings to the index
    
    return faiss_index, faq_questions, faq_answers, faq_data
# Initial load
load_faq_data()
# Create the FAISS index
# faiss_index, faq_questions, faq_answers, faq_data = create_faq_embeddings(faq_data)

def retrieve_answer(user_question: str) -> str:
    """Retrieve the most relevant FAQ answer based on the user's question."""
    
    # Create the embedding for the user query
    user_embedding = model.encode([user_question], convert_to_numpy=True)
    
    # Perform a search on the FAISS index to find the most similar FAQ questions
    distances, indices = faiss_index.search(np.array(user_embedding, dtype=np.float32), k=min(2, len(faq_questions)))
    
    # Debugging: Print the results
    print(f"FAISS search results: distances={distances}, indices={indices}")
    
    # Check if FAISS returned valid results
    if indices.shape[0] == 0 or indices[0][0] == -1:
        return "Sorry, I couldn't find an answer for that question."
    
    # Get the best match
    best_match_index = indices[0][0]
    
    # Ensure the index is valid
    if best_match_index >= len(faq_answers):
        return "Sorry, I couldn't find any relevant information for your question."
    
    best_match_question = faq_questions[best_match_index]
    best_match_answer = faq_answers[best_match_index]
    
    # Calculate similarity score (lower distance = higher similarity)
    similarity_score = 1 / (1 + distances[0][0])
    
    # If similarity is too low, indicate uncertainty
    if similarity_score < 0.3:  # Adjust threshold as needed
        return (
            f"I found something that might be related to your question:\n\n"
            f"**Q: {best_match_question}**\n"
            f"A: {best_match_answer}\n\n"
            f"If this doesn't answer your question, please feel free to ask differently or request to speak with a human."
        )
    
    # Construct the response with the matched FAQ
    response = f"Based on our FAQs:\n\n**{best_match_answer}**"
    
    # Check if there's a second relevant match
    if len(indices[0]) > 1 and indices[0][1] < len(faq_answers):
        second_match_index = indices[0][1]
        second_similarity = 1 / (1 + distances[0][1])
        
        # Only include second answer if it's reasonably similar
        if second_similarity > 0.3:
            second_answer = faq_answers[second_match_index]
            response += f"\n\nRelated information:\n{second_answer}"
    
    return response
