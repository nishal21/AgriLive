<h1 align="center">AgriLive: Multimodal Farm Assistant</h1>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10%2B-blue?style=for-the-badge&logo=python" alt="Python">
  <img src="https://img.shields.io/badge/Google%20Cloud-Vertex%20AI-4285F4?style=for-the-badge&logo=googlecloud" alt="Google Cloud">
  <img src="https://img.shields.io/badge/Docker-Ready-2496ED?style=for-the-badge&logo=docker" alt="Docker">
  <img src="https://img.shields.io/badge/License-MIT-green?style=for-the-badge" alt="License">
</p>

<p align="center">
  <blockquote><b>Built for the Gemini Live Agent Challenge 2026 (The Live Agent Track)</b></blockquote>
</p>

---

### 💡 Elevator Pitch
> "A Gemini Live Agent that diagnoses crop diseases via video and provides real-time, empathetic agricultural guidance in local languages to support farmers in crisis."

### 📺 Demo Video
[![AgriLive Demo Placeholder](https://img.shields.io/badge/YouTube-Watch%20Demo-red?style=for-the-badge&logo=youtube)](https://youtu.be/t7M9JGFdyUU)

---

## 🌍 The Problem & Impact
### Socio-Economic Context
While AgriLive was born out of the agrarian crisis in my home state of **Kerala, India**, the challenges it addresses are universal. Farmers worldwide are facing unprecedented distress due to **climate volatility**, devastating natural disasters, and invasive pests that threaten global food security.

### The Digital Divide
Traditional agricultural apps often fail those who need them most. The **digital divide** leaves elderly and rural populations across the globe struggling with complex text-based GUIs. In moments of crisis, farmers need a companion that understands their local context, their crops, and their emotions—not a maze of menus. AgriLive is designed for every farmer, from the paddy fields of Kerala to the corn belts of the world.

---

## ✨ The Solution (Key Features)

AgriLive focuses on **40% UX/UI and Innovation**, transforming the smartphone into a vital field tool:

*   **📱 Mobile-First "Field Companion" UI:** An accessible, gesture-driven design featuring a full-bleed camera interface, bottom-sheet transcripts for high readability, and a warm, earthy color palette (**Terracotta, Monsoon Teal, Paddy Green**) to build trust.
*   **🎙️ Real-Time Voice & Vision:** Powered by `gemini-live-2.5-flash-native-audio` on Vertex AI. AgriLive handles bidirectional WebSocket streaming for zero-latency, empathetic conversational support.
*   **🔍 Async Crop Analysis Agent:** A dedicated multi-agent endpoint (`/api/analyze`) using `gemini-2.5-flash`. It captures a high-res camera frame and returns a structured JSON diagnosis containing the disease name, confidence score, and organic remedies.
*   **📡 Live KVK Alerts:** Integrates **Google Search Grounding** to autonomously fetch real-time weather alerts and pest advisories from local **Krishi Vigyan Kendras (KVK)** and official Kerala agriculture portals.

---

## 🏗️ System Architecture


```mermaid
%%{init: {"flowchart": {"curve": "basis"}} }%%
flowchart TD
    %% ----------------------------------
    %% High-contrast earthy theme
    classDef frontend fill:#214D27,stroke:#4ABF76,stroke-width:2px,color:#FAF9F6,font-weight:bold,font-size:13px;
    classDef cloud fill:#2C3E34,stroke:#F4A261,stroke-width:2px,color:#FFF8E7,font-weight:bold,font-size:13px;
    classDef vertex fill:#1E3529,stroke:#90BE6D,stroke-width:2px,color:#F0F0F0,font-weight:bold,font-size:13px;
    classDef external fill:#FFF4E6,stroke:#BC6C25,stroke-width:2px,color:#1B2F1E,font-weight:bold,font-size:13px;
    classDef process fill:#FFFADD,stroke:#4ABF76,stroke-width:2px,font-weight:bold;
    linkStyle default stroke-width:2px,stroke:#A6A6A6

    %% ----------------------------------
    %% 1️⃣ Frontend Layer
    subgraph Client["📱 FIELD COMPANION (Frontend)"]
        direction TB
        UI["UI & Controls<br/><sub>HTML / CSS / JS</sub>"]:::frontend
        Mic["Microphone Stream<br/><sub>16 kHz PCM</sub>"]:::frontend
        Cam["Webcam Capture<br/><sub>1 FPS JPEG</sub>"]:::frontend
        Spk["Audio Playback<br/><sub>24 kHz PCM + Jitter Buffer</sub>"]:::frontend
    end

    %% ----------------------------------
    %% 2️⃣ Cloud Run Backend
    subgraph CloudRun["☁️ GOOGLE CLOUD RUN"]
        direction TB
        WS["WebSocket Bridge<br/><sub>3600 s Timeout + Heartbeat</sub>"]:::cloud
        API["REST API<br/><sub>/api/analyze</sub>"]:::cloud
        Fallback["Fallback Engine<br/><sub>Pydantic Parser</sub>"]:::cloud
    end

    %% ----------------------------------
    %% 3️⃣ Vertex AI
    subgraph VertexAI["🧠 VERTEX AI (Cognitive Layer)"]
        direction TB
        LiveModel["Live Agent<br/><sub>gemini-live-2.5 audio</sub>"]:::vertex
        VisionModel["Vision Agent<br/><sub>gemini-2.5 flash</sub>"]:::vertex
    end

    %% ----------------------------------
    %% 4️⃣ External Context Layer
    Search["🔍 Google Search Grounding<br/><sub>Weather / Agri Data</sub>"]:::external

    %% ----------------------------------
    %% Flow Connections
    UI -->|"Control + Transcripts"| WS
    Mic -->|"Audio Stream (WebSocket)"| WS
    Cam -->|"Single Frame Capture (HTTPS)"| API
    WS -->|"Buffered Playback"| Spk

    WS -->|"Bidirectional AI Stream"| LiveModel
    API -->|"Fallback Trigger"| Fallback
    Fallback -->|"Structured Requests"| VisionModel
    VisionModel -->|"Parsed Results"| Fallback
    LiveModel -->|"Knowledge Lookup"| Search
    Search -->|"Real-time Context"| LiveModel
    LiveModel -->|"AI Responses"| WS

    %% ----------------------------------
    %% Narrative Summary (Readable captions)
    step1["👩‍🌾 User Interaction"]:::process
    step2["☁️ Cloud Processing"]:::process
    step3["🧠 AI Reasoning"]:::process
    step4["🌦️ Grounded Knowledge"]:::process
    step5["🔊 Response Delivery"]:::process

    %% Connect summary steps vertically
    step1 --> Client
    Client --> step2 --> CloudRun --> step3 --> VertexAI --> step4 --> Search --> step5 --> Spk
```


### Data Flow & Implementation
1.  **Frontend:** A Vanilla JS/HTML5 implementation using WebRTC to capture raw **16-bit PCM audio** (16kHz) and **1FPS JPEG frames**. It features robust exponential backoff logic to handle mobile network drops in remote fields.
2.  **Backend:** A high-performance **FastAPI** server containerized with Docker and deployed on **Google Cloud Run**.
3.  **AI Engine:** Uses the **Google GenAI SDK** to route streams to **Vertex AI**. The server maintains a stateful WebSocket session, handling autonomous tool calls (Search Grounding) and multimodal context.

---

## 🚀 Setup & Deployment

### Local Installation
1. **Clone & Setup Environment:**
   ```bash
   # Create a virtual environment
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate

   # Install dependencies
   pip install -r requirements.txt
   ```

2. **Configure Environment:**
   Create a `.env` file or set the following:
   ```bash
   export GOOGLE_CLOUD_PROJECT="your-project-id"
   export GOOGLE_CLOUD_LOCATION="us-central1"
   ```

3. **Run Locally:**
   ```bash
   uvicorn main:app --reload
   ```

### Cloud Run Deployment
Deploy the assistant to Google Cloud Run with WebSocket support enabled:
```bash
gcloud run deploy agrilive-assistant \
    --source . \
    --region us-central1 \
    --project [YOUR_PROJECT_ID] \
    --timeout=3600 \
    --allow-unauthenticated
```
> **Note:** The `--timeout=3600` flag is critical to ensure WebSocket sessions are not prematurely terminated by the Cloud Run ingress.

---

## 🛠️ Technologies Used

- **Language:** Python 3.10+
- **Framework:** FastAPI
- **Communication:** WebSockets, WebRTC
- **Cloud:** Google Cloud Run, Vertex AI
- **AI Models:** 
  - `gemini-live-2.5-flash-native-audio` (Live Agent)
  - `gemini-2.5-flash` (Vision Analysis Agent)
- **Features:** Google Search Grounding
- **Frontend:** Vanilla HTML5, CSS3 (Custom Design System), JavaScript

---

<p align="center">Made with ❤️ for the Farming Community</p>
