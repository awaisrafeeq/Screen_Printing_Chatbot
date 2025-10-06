# rag_system.py - FIXED VERSION
import faiss
import numpy as np
from sentence_transformers import SentenceTransformer
from flows.pdf_extractor import extract_faq_data, extract_pdf_text

# Load the Sentence-BERT model for embedding FAQ questions and answers
model = SentenceTransformer('all-MiniLM-L6-v2')

# Extract FAQ data from PDF (replace with your actual file path)
pdf_text = extract_pdf_text(r"C:\Users\PMLS\Downloads\intents_chat_bot_self_workingupdated (1)\intents_chat_bot_self\FAQ For quotes.pdf")
faq_data = extract_faq_data(pdf_text)

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

# Create the FAISS index
faiss_index, faq_questions, faq_answers, faq_data = create_faq_embeddings(faq_data)

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