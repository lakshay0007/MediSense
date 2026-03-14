# MediSense — Remote Emergency Healthcare Co-Pilot

Real-time multimodal AI assistance for healthcare professionals in rural clinics and home-care settings, powered by the **Gemini Multimodal Live API**.

## The Problem

Junior nurses and caregivers in resource-limited settings often need to perform complex clinical procedures without immediate access to expert physicians. Traditional telemedicine is slow and requires scheduling. There is a gap between what a nurse can do alone and what they can do with real-time expert guidance.

## The Solution

MediSense uses the Gemini **Multimodal Live API** to act as a real-time AI co-pilot. It simultaneously processes:

- 📷 **Live camera feed** — wound assessment, skin conditions, equipment error codes, patient appearance
- 🖥️ **Screen share** — EHR data, vital monitor readings, lab results, imaging, medical device displays
- 🎤 **Voice queries** — sub-second latency, barge-in capable ("Wait, look at this instead")
- 🖼️ **Uploaded images** — X-rays, lab reports, medication labels

## Key Features

| Feature | Description |
|---|---|
| **Voice Interaction** | Real-time voice conversation with barge-in support |
| **Text Input** | Type queries when in quiet areas |
| **Camera Share** | Point at patient or equipment for visual analysis |
| **Screen Share** | Share EHR, monitors, or clinical apps |
| **Image Upload** | Attach X-rays, lab results, images |
| **Clinical Log** | MediSense auto-logs key observations as notes |
| **Urgent Alerts** | Critical findings trigger a prominent dashboard alert |
| **Log Export** | Download the full clinical log as a text file |
| **Session Resume** | 30-minute conversation history with auto-reconnection |

## Example Interaction

> **Nurse** *(pointing camera at patient's arm)*: "The patient's heart rate is spiking on the monitor, and I'm seeing this specific discoloration here. Based on their history on my screen, should I adjust the dosage?"

> **MediSense** *(via voice)*: "⚠️ I can see the redness extending proximally. Given the hypertension history visible on your screen, do NOT adjust the dosage yet. Step 1: Check if the oxygen lead is secure — it looks slightly loose in your video feed. Step 2: Re-check blood pressure manually. Step 3: If BP is above 160, contact the supervising physician immediately."

## Prerequisites

- Python 3.10+
- Google Cloud project with Vertex AI API enabled

## Setup

1. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

2. **Run the application:**
   ```bash
   python app.py
   ```

3. **Open** http://localhost:8081 in your browser.

## Authentication

1. Enter your **Google Cloud Project ID** in the sidebar
2. Click **"Open Cloud Shell"** and run: `gcloud auth print-access-token`
3. Paste the token and click **"Validate & Connect"**
4. Click **"▶ Start Session"** to connect to Gemini Live

> Access tokens expire after ~1 hour. Regenerate as needed.

## Safety Disclaimer

MediSense is an AI assistant **only**. It is **not** a substitute for professional medical judgment. In any life-threatening emergency, call 911 (or your local emergency services) immediately. Drug dosage recommendations must be verified by a qualified physician before implementation.
