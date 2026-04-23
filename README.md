# 🚨 CrisisSense AI

AI-powered emergency response system that detects, analyzes, and structures crisis messages in real-time.

## 🎯 Objective
Detect emergencies instantly, classify them, and provide actionable steps while simulating response coordination.

## 🔹 Features
- Emergency Type Detection (Fire, Medical, Security, Accident, Disaster)
- Urgency & Panic Level Analysis
- AI-powered (Groq - LLaMA 3.3)
- Fallback system (works without AI)
- Step-by-step safety guidance
- Simulated dispatch (ambulance / police / fire)
- Accessibility support (contrast, text, motion)

## 🔹 Tech Stack
Frontend: HTML, CSS, JavaScript  
Backend: Flask (Python)  
AI: Groq API (LLaMA 3.3)

## 🔹 Setup

1. Install dependencies:
pip install flask flask-cors groq

2. Set API key:

CMD:
set GROQ_API_KEY=your_key_here

PowerShell:
$env:GROQ_API_KEY="your_key_here"

3. Run backend:
python app.py

4. Run frontend:
Open frontend/index.html in browser

## 🔹 Example

Input:
fire in kitchen help

Output:
Emergency Type: FIRE
Urgency: HIGH
Panic Level: HIGH

Steps:
1. Evacuate immediately
2. Avoid smoke
3. Call fire services

## 🔹 Note
This is a prototype for demonstration purposes. Not connected to real emergency systems.

## 🚀 Future Scope
- GPS integration
- Real emergency APIs
- Voice input
- Multi-language support
