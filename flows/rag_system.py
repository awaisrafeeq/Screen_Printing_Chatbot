import faiss
import numpy as np
from sentence_transformers import SentenceTransformer
from flows.pdf_extractor import extract_faq_data, extract_pdf_text
import requests
import os
import time
import tempfile

# Load the Sentence-BERT model for embedding FAQ questions and answers
model = SentenceTransformer('all-MiniLM-L6-v2')

# Global variables for FAQ data and last update time
faq_data = None
faiss_index = None
faq_questions = None
faq_answers = None
last_update_time = 0

def load_faq_data():
    """Load FAQ data from PDF with 3-minute cache"""
    global faq_data, faiss_index, faq_questions, faq_answers, last_update_time
    
    # Check if we need to reload (every 3 minutes for testing)
    current_time = time.time()
    three_minutes_seconds = 3 * 60  # 180 seconds
    
    if current_time - last_update_time > three_minutes_seconds:
        print("🔄 Reloading FAQ data from PDF...")
        # Download the PDF from GitHub
        url = "https://raw.githubusercontent.com/awaisrafeeq/Screen_Printing_Chatbot/master/FAQ%20For%20quotes.pdf"
        local_path = "/tmp/FAQ.pdf"  # Use /tmp for Render's ephemeral filesystem
        try:
            response = requests.get(url)
            response.raise_for_status()  # Check for download errors
            with open(local_path, 'wb') as f:
                f.write(response.content)
            pdf_text = extract_pdf_text(local_path)
            faq_data = extract_faq_data(pdf_text)
            if not faq_data:  # Handle empty FAQ data
                print("⚠️ No FAQs extracted from PDF")
                faq_data = {}
                faiss_index = faiss.IndexFlatL2(384)  # Default dimension for all-MiniLM-L6-v2
                faq_questions = []
                faq_answers = []
            else:
                faiss_index, faq_questions, faq_answers, _ = create_faq_embeddings(faq_data)
            last_update_time = current_time
            print(f"✅ FAQ data reloaded with {len(faq_data)} items")
        except Exception as e:
            print(f"Error downloading or processing PDF: {e}")
            if faq_data is None:  # Fallback if first load fails
                faq_data = {}
                faiss_index = faiss.IndexFlatL2(384)  # Default dimension for all-MiniLM-L6-v2
                faq_questions = []
                faq_answers = []
    else:
        print("📚 Using cached FAQ data")

def create_faq_embeddings(faq_data: dict):
    """Creates embeddings for FAQ questions and indexes them using FAISS."""
    faq_questions = list(faq_data.keys())
    faq_answers = list(faq_data.values())
    
    # Handle empty FAQ data
    if not faq_questions:
        return faiss.IndexFlatL2(384), [], [], faq_data
    
    # Create embeddings for questions only
    faq_embeddings = model.encode(faq_questions, convert_to_numpy=True)
    
    # Create FAISS index
    dimension = faq_embeddings.shape[1]  # Dimensionality of the embeddings
    faiss_index = faiss.IndexFlatL2(dimension)  # Using L2 distance for similarity
    faiss_index.add(np.array(faq_embeddings, dtype=np.float32))  # Adding embeddings to the index
    
    return faiss_index, faq_questions, faq_answers, faq_data

# Initial load
load_faq_data()

def retrieve_answer(user_question: str) -> str:
    """Retrieve the most relevant FAQ answer based on the user's question."""
    # Ensure FAQ data is loaded
    if faq_data is None:
        load_faq_data()
    
    # Handle empty FAQ case
    if not faq_questions:
        return "Sorry, no FAQ data is available at the moment."
    
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
