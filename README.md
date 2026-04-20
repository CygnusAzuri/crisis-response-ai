# 🚨 CrisisSense AI

AI-powered emergency analyzer that detects and structures crisis messages in real-time.

## 🔹 Features
- Emergency Type Detection (Fire, Medical, Security)
- Urgency & Panic Level Analysis
- AI-powered (Groq - LLaMA 3.3)
- Fallback system (always works)
- Simulated reporting system

## 🔹 Tech Stack
- Frontend: HTML, JavaScript
- Backend: Flask (Python)
- AI: Groq API (LLaMA 3.3)

## 🔹 Setup

1. Install dependencies:
pip install flask flask-cors groq

2. Set API key:
Windows:
set GROQ_API_KEY=your_key_here

3. Run backend:
python app.py

4. Open frontend:
Open index.html in browser

## 🔹 Example

Input:
fire in kitchen help

Output:
Type: Fire  
Urgency: High  
Panic Level: High  

## 🔹 Note
This is a prototype demonstrating AI-based crisis detection and reporting.
