"""MediSense - Remote Emergency Healthcare Co-Pilot powered by Gemini Live API.

Enables real-time voice + video + screen analysis for junior nurses and caregivers
in rural clinics and home-care settings via the Gemini Multimodal Live API.
"""

import asyncio
import base64
import logging
import os
import re
import threading
import traceback
from datetime import datetime
from pathlib import Path

import google.auth
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
from flask_socketio import SocketIO, emit
from google import genai
from google.genai import types
from google.oauth2.credentials import Credentials

try:
    import io as pil_io
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

app = Flask(__name__, static_folder="src", static_url_path="")
CORS(app)
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

socketio = SocketIO(
    app,
    cors_allowed_origins="*",
    async_mode="threading",
    ping_timeout=300,
    ping_interval=25,
    max_http_buffer_size=50000000,
    transports=["polling"],
)

# Configuration
DEFAULT_PROJECT_ID = os.getenv("GOOGLE_CLOUD_PROJECT", "geminihackathon7")
DEFAULT_LOCATION_ID = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")

GEMINI_LIVE_MODEL = "gemini-live-2.5-flash-native-audio"
GEMINI_IMAGE_MODEL = "gemini-2.0-flash-preview-image-generation"

# Global state
session_credentials = {}
bridges = {}
live_sessions = {}
starting_sessions = set()
starting_session_sids = {}
session_states = {}
session_handles = {}
clinical_notes = []
user_name = "Rama"
current_mode = "nurse"  # "nurse" or "patient"
active_patient = None          # patient ID of currently examined patient (nurse mode)
logged_in_patient_id = None    # patient who logged in via patient mode self-login
active_procedure = None        # currently active procedure checklist

# ── Procedure checklist library ───────────────────────────────────────────────
PROCEDURE_LIBRARY = [
    {
        "id": "iv_cannulation",
        "name": "IV Cannulation",
        "icon": "💉",
        "category": "Vascular Access",
        "steps": [
            {"step": 1, "title": "Verify patient identity & consent", "detail": "Confirm patient name, DOB, wristband. Explain procedure and obtain verbal consent.", "visual_check": "Wristband visible on camera"},
            {"step": 2, "title": "Hand hygiene & don gloves", "detail": "Perform WHO 5-moment hand hygiene. Don non-sterile gloves.", "visual_check": "Gloves on both hands"},
            {"step": 3, "title": "Prepare equipment", "detail": "Gather cannula (appropriate gauge), tourniquet, antiseptic swab, transparent dressing, saline flush, sharps bin.", "visual_check": "Equipment laid out on tray"},
            {"step": 4, "title": "Apply tourniquet & select vein", "detail": "Apply tourniquet 10-15 cm above intended site. Palpate for bouncy, well-filled vein. Avoid joints and bruised areas.", "visual_check": "Tourniquet applied, vein visible"},
            {"step": 5, "title": "Clean insertion site", "detail": "Clean skin with antiseptic swab using back-and-forth strokes for 30 seconds. Allow to air dry completely.", "visual_check": "Swabbing motion visible"},
            {"step": 6, "title": "Insert cannula", "detail": "Anchor vein distally. Insert cannula bevel-up at 15-30° angle. Watch for flashback in chamber. Advance cannula, withdraw needle.", "visual_check": "Flashback confirmed in chamber"},
            {"step": 7, "title": "Secure & flush", "detail": "Release tourniquet. Apply transparent dressing. Flush with 5-10 mL NaCl 0.9%. Document date, gauge, site, and attempts.", "visual_check": "Dressing applied, flush connected"},
        ],
    },
    {
        "id": "wound_assessment",
        "name": "Wound Assessment & Dressing",
        "icon": "🩹",
        "category": "Wound Care",
        "steps": [
            {"step": 1, "title": "Hand hygiene & prepare clean field", "detail": "Wash hands. Set up sterile dressing pack on a clean surface. Don sterile gloves.", "visual_check": "Sterile field visible"},
            {"step": 2, "title": "Remove old dressing", "detail": "Gently remove existing dressing. Note any adherence, odour, or discharge on the old dressing.", "visual_check": "Old dressing removed; wound exposed"},
            {"step": 3, "title": "Assess wound bed", "detail": "Measure wound dimensions (L × W × D). Note tissue type: granulation (red), slough (yellow), necrotic (black), epithelialising (pink).", "visual_check": "Wound bed clearly visible on camera"},
            {"step": 4, "title": "Check wound edges & surrounding skin", "detail": "Assess for erythema, warmth, swelling, maceration, undermining, or tunnelling.", "visual_check": "Wound edges and peri-wound skin visible"},
            {"step": 5, "title": "Clean wound", "detail": "Irrigate with NaCl 0.9% or prescribed solution. Clean from centre outward. Pat dry surrounding skin.", "visual_check": "Irrigation in progress"},
            {"step": 6, "title": "Apply appropriate dressing", "detail": "Select dressing based on wound type and exudate level. Apply primary and secondary dressing layers.", "visual_check": "New dressing being applied"},
            {"step": 7, "title": "Document findings", "detail": "Record wound size, tissue type, exudate, peri-wound condition, dressing used, and next review date.", "visual_check": "Documentation complete"},
        ],
    },
    {
        "id": "vitals_assessment",
        "name": "Full Vital Signs Assessment",
        "icon": "📊",
        "category": "Assessment",
        "steps": [
            {"step": 1, "title": "Introduce & position patient", "detail": "Explain the procedure. Patient seated or supine for at least 5 minutes. Arm at heart level.", "visual_check": "Patient positioned correctly"},
            {"step": 2, "title": "Measure blood pressure", "detail": "Apply cuff to bare upper arm. Use correct cuff size. Record systolic/diastolic.", "visual_check": "BP cuff applied correctly"},
            {"step": 3, "title": "Measure heart rate & rhythm", "detail": "Palpate radial pulse for 60 seconds. Note rate, rhythm (regular/irregular), and strength.", "visual_check": "Fingers on radial pulse"},
            {"step": 4, "title": "Measure respiratory rate", "detail": "Count breaths for 60 seconds without patient awareness. Note depth and pattern.", "visual_check": "Observing chest rise"},
            {"step": 5, "title": "Measure SpO₂", "detail": "Apply pulse oximeter to finger. Ensure good waveform. Record reading.", "visual_check": "Pulse oximeter on finger"},
            {"step": 6, "title": "Measure temperature", "detail": "Use tympanic or temporal thermometer. Record in °C.", "visual_check": "Thermometer in use"},
            {"step": 7, "title": "Assess consciousness (AVPU/GCS)", "detail": "Alert / Voice / Pain / Unresponsive. Calculate GCS if neurological concern.", "visual_check": "Assessing patient response"},
            {"step": 8, "title": "Calculate & record NEWS2", "detail": "Sum all vital sign sub-scores. Escalate per local protocol if score ≥ 5 or single parameter = 3.", "visual_check": "NEWS2 chart visible"},
        ],
    },
    {
        "id": "catheter_care",
        "name": "Urinary Catheter Care",
        "icon": "🏥",
        "category": "Catheter Management",
        "steps": [
            {"step": 1, "title": "Hand hygiene & don gloves", "detail": "Perform hand hygiene. Don clean non-sterile gloves.", "visual_check": "Gloves on"},
            {"step": 2, "title": "Inspect catheter system", "detail": "Check catheter is draining freely, no kinks or tension. Bag below bladder level. Note urine colour, clarity, volume.", "visual_check": "Drainage bag visible"},
            {"step": 3, "title": "Clean meatal area", "detail": "Clean around catheter insertion site with soap and water. Wipe away from meatus. Pat dry.", "visual_check": "Cleaning at meatus"},
            {"step": 4, "title": "Check balloon volume", "detail": "Verify balloon is inflated to recommended volume (usually 10 mL). Do not over-inflate.", "visual_check": "Balloon port visible"},
            {"step": 5, "title": "Empty drainage bag", "detail": "Use no-touch technique on outlet tap. Empty into measuring jug. Record volume. Clean tap after.", "visual_check": "Emptying via outlet tap"},
            {"step": 6, "title": "Assess for complications", "detail": "Check for bypassing, pain, haematuria, cloudy/foul-smelling urine, blocked catheter.", "visual_check": "Urine sample if needed"},
            {"step": 7, "title": "Document", "detail": "Record urine output, catheter condition, any concerns, next review date.", "visual_check": "Documentation complete"},
        ],
    },
    {
        "id": "blood_glucose",
        "name": "Blood Glucose Monitoring",
        "icon": "🩸",
        "category": "Monitoring",
        "steps": [
            {"step": 1, "title": "Verify patient & check timing", "detail": "Confirm patient identity. Check if fasting or post-meal reading. Review insulin/medication schedule.", "visual_check": "Patient identified"},
            {"step": 2, "title": "Prepare glucometer", "detail": "Turn on glucometer. Insert test strip. Confirm calibration code matches strip vial.", "visual_check": "Glucometer ready with strip"},
            {"step": 3, "title": "Hand hygiene & prepare site", "detail": "Clean patient's finger with alcohol swab. Allow to dry completely. Select side of fingertip (less painful).", "visual_check": "Finger cleaned"},
            {"step": 4, "title": "Obtain blood sample", "detail": "Use lancet on side of fingertip. Gently squeeze to form a hanging drop. Do not milk excessively.", "visual_check": "Blood drop forming"},
            {"step": 5, "title": "Apply blood to test strip", "detail": "Touch blood drop to test strip edge. Ensure adequate sample. Wait for result.", "visual_check": "Glucometer reading"},
            {"step": 6, "title": "Record & act on result", "detail": "Document result. If hypo (<4 mmol/L): give fast-acting glucose. If hyper (>15 mmol/L): check ketones, contact doctor.", "visual_check": "Reading on display"},
        ],
    },
]

# Auto-authenticate from GCLOUD_ACCESS_TOKEN environment variable if present
_env_token = os.getenv("GCLOUD_ACCESS_TOKEN", "").strip()
if _env_token:
    session_credentials["oauth"] = {
        "credentials": Credentials(token=_env_token),
        "project_id": DEFAULT_PROJECT_ID,
        "location": DEFAULT_LOCATION_ID,
        "access_token": _env_token,
    }
    logging.info("Pre-authenticated via GCLOUD_ACCESS_TOKEN environment variable")

# ── Sample patient dataset ────────────────────────────────────────────────────
SAMPLE_PATIENTS = [
    {
        "id": "P001",
        "name": "Margaret Chen",
        "age": 68, "dob": "1957-11-14", "gender": "Female", "blood_type": "A+",
        "allergies": ["Penicillin", "Sulfa drugs"],
        "chief_complaint": "Persistent joint pain, fatigue, low-grade fever",
        "diagnoses": ["Rheumatoid Arthritis (active)", "Type 2 Diabetes Mellitus", "Hypertension"],
        "medications": [
            {"name": "Methotrexate",  "dose": "15 mg",   "frequency": "Weekly"},
            {"name": "Prednisone",    "dose": "5 mg",    "frequency": "Daily"},
            {"name": "Metformin",     "dose": "1000 mg", "frequency": "Twice daily"},
            {"name": "Lisinopril",    "dose": "10 mg",   "frequency": "Daily"},
            {"name": "Folic Acid",    "dose": "1 mg",    "frequency": "Daily"},
        ],
        "vitals": {
            "bp": "148/92 mmHg", "hr": "88 bpm", "rr": "18 /min",
            "spo2": "97%", "temp": "37.8°C", "weight": "72 kg", "pain_score": "6/10",
        },
        "labs": {
            "esr": [
                {"date": "2026-03-08", "value": 78,  "unit": "mm/hr", "ref": "0–20"},
                {"date": "2026-02-01", "value": 64,  "unit": "mm/hr", "ref": "0–20"},
                {"date": "2026-01-03", "value": 52,  "unit": "mm/hr", "ref": "0–20"},
                {"date": "2025-12-06", "value": 41,  "unit": "mm/hr", "ref": "0–20"},
            ],
            "crp":         "42 mg/L (HIGH, ref <5)",
            "hba1c":       "7.8% (ref <7%)",
            "wbc":         "11.2 × 10³/µL (HIGH, ref 4.5–11.0)",
            "hgb":         "10.4 g/dL (LOW, ref 12.0–16.0)",
            "platelets":   "412 × 10³/µL (ref 150–400)",
            "creatinine":  "1.1 mg/dL (ref 0.6–1.1)",
            "rf":          "Positive (164 IU/mL)",
            "anti_ccp":    "Positive (>250 U/mL)",
        },
        "visit_notes": [
            {"date": "2026-02-01", "note": "RA flare. Increased swelling MCP/PIP joints. ESR elevated. Prednisone maintained. Rheumatology referral placed."},
            {"date": "2026-01-03", "note": "Follow-up. 40% improvement in morning stiffness. MTX dose increased 10→15 mg."},
            {"date": "2025-12-06", "note": "Initial visit. 3-month bilateral hand/wrist swelling. RF and anti-CCP positive. Seropositive RA confirmed."},
        ],
        "imaging": [
            {"date": "2026-02-01", "type": "X-Ray", "region": "Bilateral Hands AP", "report": "Periarticular osteopenia at MCP and PIP joints bilaterally. Soft tissue swelling at 2nd–4th MCP joints. Early marginal erosions at 2nd and 3rd MCP joints (left > right). Joint space narrowing at PIP joints. No fractures. No calcifications.", "impression": "Findings consistent with active rheumatoid arthritis with early erosive changes. Recommend correlation with inflammatory markers."},
            {"date": "2025-12-06", "type": "X-Ray", "region": "Chest PA", "report": "Heart size normal. Lungs are clear bilaterally. No pleural effusion. No consolidation. Mediastinal contours normal. Bony structures unremarkable.", "impression": "Normal chest radiograph. No acute cardiopulmonary disease."},
        ],
    },
    {
        "id": "P002",
        "name": "Ravi Sharma",
        "age": 45, "dob": "1980-06-22", "gender": "Male", "blood_type": "B+",
        "allergies": ["None known"],
        "chief_complaint": "Chest tightness & shortness of breath on exertion",
        "diagnoses": ["Unstable Angina", "Hyperlipidemia", "Obstructive Sleep Apnea"],
        "medications": [
            {"name": "Aspirin",           "dose": "81 mg",   "frequency": "Daily"},
            {"name": "Atorvastatin",      "dose": "40 mg",   "frequency": "Nightly"},
            {"name": "Nitroglycerin SL",  "dose": "0.4 mg",  "frequency": "PRN chest pain"},
            {"name": "Metoprolol",        "dose": "25 mg",   "frequency": "Twice daily"},
        ],
        "vitals": {
            "bp": "162/98 mmHg", "hr": "96 bpm", "rr": "22 /min",
            "spo2": "95%", "temp": "37.1°C", "weight": "94 kg", "pain_score": "7/10 chest",
        },
        "labs": {
            "esr": [
                {"date": "2026-03-08", "value": 34, "unit": "mm/hr", "ref": "0–15"},
                {"date": "2026-01-15", "value": 28, "unit": "mm/hr", "ref": "0–15"},
            ],
            "troponin_i":   "0.08 ng/mL (BORDERLINE HIGH, ref <0.04)",
            "bnp":          "180 pg/mL (HIGH, ref <100)",
            "ldl":          "148 mg/dL (HIGH, ref <100)",
            "hdl":          "38 mg/dL (LOW, ref >40)",
            "triglycerides":"220 mg/dL (HIGH, ref <150)",
            "ecg":          "ST depression 1 mm in V4–V6",
            "wbc":          "9.4 × 10³/µL (normal)",
            "hgb":          "14.2 g/dL (normal)",
            "creatinine":   "1.0 mg/dL (normal)",
        },
        "visit_notes": [
            {"date": "2026-03-08", "note": "URGENT. Chest tightness at rest ×2 hrs. ST changes on ECG. Troponin borderline elevated. Cardiology consult requested. NBM. IV access established."},
            {"date": "2026-01-15", "note": "Stress test: exercise-induced ST changes at moderate workload. Statin initiated. Cardiology referral submitted."},
        ],
        "imaging": [
            {"date": "2026-03-08", "type": "ECG", "region": "12-Lead ECG", "report": "Rate: 96 bpm. Rhythm: Normal sinus rhythm. Axis: Normal. PR interval: 180 ms. QRS: 90 ms. ST segment: 1 mm horizontal ST depression in leads V4, V5, V6. T-wave: Flattened in lateral leads. No pathological Q waves.", "impression": "ST depression in lateral leads — consistent with myocardial ischaemia. Urgent cardiology review recommended."},
            {"date": "2026-03-08", "type": "X-Ray", "region": "Chest PA", "report": "Mild cardiomegaly (CTR 0.55). Upper lobe pulmonary venous distension suggesting early pulmonary congestion. No pleural effusion. No consolidation. Aortic arch mildly calcified.", "impression": "Mild cardiomegaly with early signs of pulmonary congestion. Clinical correlation with cardiac enzymes and echocardiography advised."},
            {"date": "2026-01-15", "type": "Stress Test", "region": "Exercise ECG Stress Test", "report": "Protocol: Bruce. Duration: 6 min 42 sec (Stage 2). Achieved 85% max predicted HR (158/186 bpm). At peak exercise: 1.5 mm horizontal ST depression in V4–V6. Patient reported chest tightness. Recovery: ST changes resolved at 5 min post-exercise.", "impression": "Positive for exercise-induced ischaemia at moderate workload. Significant coronary artery disease suspected."},
        ],
    },
    {
        "id": "P003",
        "name": "Amalia Torres",
        "age": 29, "dob": "1996-03-18", "gender": "Female", "blood_type": "O-",
        "allergies": ["Latex", "NSAIDs (GI intolerance)"],
        "chief_complaint": "Post-op wound care — Day 5 after laparoscopic appendectomy",
        "diagnoses": ["Status post laparoscopic appendectomy", "Suspected wound site infection"],
        "medications": [
            {"name": "Cefazolin",    "dose": "1 g IV",   "frequency": "Every 8 hours"},
            {"name": "Paracetamol", "dose": "1000 mg",  "frequency": "Every 6 hours PRN"},
            {"name": "Ondansetron", "dose": "4 mg IV",  "frequency": "Every 8 hours PRN nausea"},
        ],
        "vitals": {
            "bp": "118/74 mmHg", "hr": "102 bpm", "rr": "20 /min",
            "spo2": "99%", "temp": "38.4°C", "weight": "58 kg", "pain_score": "4/10",
        },
        "labs": {
            "esr": [
                {"date": "2026-03-08", "value": 55, "unit": "mm/hr", "ref": "0–20"},
                {"date": "2026-03-04", "value": 38, "unit": "mm/hr", "ref": "0–20"},
            ],
            "wbc":          "14.8 × 10³/µL (HIGH, ref 4.5–11.0)",
            "neutrophils":  "82% (HIGH, ref 50–70%)",
            "crp":          "68 mg/L (HIGH, ref <5)",
            "wound_culture":"Pending — sent 2026-03-07",
            "hgb":          "11.8 g/dL (LOW-NORMAL, ref 12.0–16.0)",
            "creatinine":   "0.7 mg/dL (normal)",
        },
        "visit_notes": [
            {"date": "2026-03-08", "note": "Day 5 post-op. Febrile 38.4°C. Wound: erythema 3 cm around incision, mild purulent discharge. Warmth and tenderness on palpation. Wound swab sent. IV antibiotics continued."},
            {"date": "2026-03-06", "note": "Day 3 post-op. Low-grade fever 37.9°C. Wound healing expected. Monitor."},
            {"date": "2026-03-03", "note": "Laparoscopic appendectomy completed. No intra-op complications. Discharged to ward."},
        ],
        "imaging": [
            {"date": "2026-03-03", "type": "CT Scan", "region": "Abdomen & Pelvis with IV Contrast", "report": "FINDINGS: Dilated appendix (12 mm diameter) with mural enhancement. Periappendiceal fat stranding and a small amount of free fluid in the right iliac fossa. A 5 mm appendicolith is identified at the base. No abscess formation. No free air. Liver, spleen, kidneys unremarkable. No lymphadenopathy.", "impression": "Acute appendicitis with appendicolith. No perforation or abscess. Surgical consultation recommended."},
            {"date": "2026-03-08", "type": "Ultrasound", "region": "RIF Surgical Site", "report": "A 1.8 × 0.9 cm hypoechoic collection is noted superficial to the rectus sheath at the port site (RIF). Mild surrounding soft-tissue oedema. No deep intra-abdominal collection. No free fluid.", "impression": "Small superficial collection at surgical port site — likely post-operative seroma vs early abscess. Recommend clinical correlation and consider aspiration if worsening."},
        ],
    },
    {
        "id": "P004",
        "name": "George Okafor",
        "age": 72, "dob": "1953-09-05", "gender": "Male", "blood_type": "AB+",
        "allergies": ["Morphine (respiratory depression)", "Iodine contrast"],
        "chief_complaint": "Acute confusion, falls at home, poor oral intake",
        "diagnoses": ["Acute delirium (likely UTI-induced)", "CKD Stage 3b", "Benign Prostatic Hyperplasia", "Osteoarthritis"],
        "medications": [
            {"name": "Tamsulosin",    "dose": "0.4 mg",  "frequency": "Daily"},
            {"name": "Amlodipine",    "dose": "5 mg",    "frequency": "Daily"},
            {"name": "Paracetamol",   "dose": "500 mg",  "frequency": "Twice daily PRN"},
            {"name": "Nitrofurantoin","dose": "100 mg",  "frequency": "Twice daily (new)"},
        ],
        "vitals": {
            "bp": "108/64 mmHg", "hr": "112 bpm", "rr": "24 /min",
            "spo2": "94%", "temp": "38.9°C", "weight": "68 kg",
            "gcs": "11/15", "pain_score": "Unable to assess reliably",
        },
        "labs": {
            "esr": [
                {"date": "2026-03-08", "value": 92, "unit": "mm/hr", "ref": "0–20"},
            ],
            "wbc":          "16.2 × 10³/µL (HIGH, ref 4.5–11.0)",
            "crp":          "112 mg/L (HIGH, ref <5)",
            "urine_analysis":"Nitrites +, Leukocytes 3+, WBC >50/HPF — consistent with UTI",
            "urine_culture": "Pending",
            "creatinine":   "2.1 mg/dL (HIGH, ref 0.7–1.3)",
            "egfr":         "32 mL/min/1.73m² (CKD Stage 3b)",
            "sodium":        "128 mEq/L (LOW — hyponatremia)",
            "potassium":     "5.4 mEq/L (HIGH, ref 3.5–5.0)",
            "lactate":       "2.1 mmol/L (BORDERLINE, ref <2.0)",
        },
        "visit_notes": [
            {"date": "2026-03-08", "note": "URGENT. Confused since morning. SpO2 94% on air. Tachycardic + febrile. UA positive for UTI. Hyponatremia noted. IV fluids started. CAUTION: CKD — avoid nephrotoxics. MORPHINE CONTRAINDICATED."},
        ],
        "imaging": [
            {"date": "2026-03-08", "type": "X-Ray", "region": "Chest PA", "report": "Heart size at upper limit of normal. Bilateral basal atelectasis. No consolidation. No pleural effusion. Mild degenerative changes in the thoracic spine. Medical devices: No lines or tubes.", "impression": "No acute pulmonary pathology. Basal atelectasis — likely positional. Consider repeat if clinical concern for aspiration."},
            {"date": "2026-03-08", "type": "CT Scan", "region": "Head Non-Contrast", "report": "No acute intracranial haemorrhage. No midline shift. Ventricles mildly prominent — age-appropriate. Mild periventricular white matter hypodensity consistent with chronic small vessel ischaemic disease. No acute territorial infarct. Calvarium intact.", "impression": "No acute intracranial pathology. Chronic small vessel disease. Clinical correlation with metabolic panel for delirium workup."},
            {"date": "2026-03-08", "type": "Ultrasound", "region": "Renal (KUB)", "report": "Right kidney: 10.2 cm, normal cortical thickness, no hydronephrosis, no calculi. Left kidney: 9.8 cm, mild cortical thinning, no hydronephrosis, no calculi. Bladder: 420 mL residual volume (elevated). Prostate: enlarged, estimated 55 g.", "impression": "Bilateral kidneys consistent with CKD. Elevated post-void residual suggesting BPH-related outlet obstruction. No obstructive uropathy."},
        ],
    },
    {
        "id": "P005",
        "name": "Lily Nguyen",
        "age": 8, "dob": "2017-12-10", "gender": "Female", "blood_type": "A-",
        "allergies": ["Amoxicillin (rash)"],
        "chief_complaint": "Severe asthma exacerbation — poor response to home nebuliser",
        "diagnoses": ["Acute severe asthma exacerbation", "Atopic dermatitis", "Allergic rhinitis"],
        "medications": [
            {"name": "Salbutamol nebuliser",   "dose": "2.5 mg",     "frequency": "Every 20 min ×3, then reassess"},
            {"name": "Ipratropium nebuliser",  "dose": "250 mcg",    "frequency": "Every 20 min ×3"},
            {"name": "Prednisolone",           "dose": "1 mg/kg (max 40 mg)", "frequency": "Once daily ×3 days"},
            {"name": "Fluticasone inhaler",    "dose": "100 mcg",    "frequency": "Twice daily (maintenance)"},
            {"name": "Montelukast",            "dose": "5 mg",       "frequency": "Nightly"},
        ],
        "vitals": {
            "bp": "100/62 mmHg", "hr": "138 bpm", "rr": "38 /min",
            "spo2": "89% on air → 94% on O2 4L/min", "temp": "37.4°C", "weight": "26 kg",
            "pefr": "32% predicted (ref: >50% moderate, <33% severe)",
            "accessory_muscles": "Yes — intercostal & subcostal retractions",
        },
        "labs": {
            "esr": [
                {"date": "2026-03-08", "value": 28, "unit": "mm/hr", "ref": "0–20"},
            ],
            "abg":          "pH 7.38 | PaO2 62 mmHg | PaCO2 42 mmHg | HCO3 24 — watch for rising CO2",
            "wbc":          "13.1 × 10³/µL (mild elevation, likely stress response)",
            "eosinophils":  "8% (HIGH — suggests atopic component)",
            "ige":          "480 IU/mL (HIGH, ref <90 for age)",
            "crp":          "8 mg/L (mildly elevated)",
            "chest_xray":   "Hyperinflation bilaterally. No consolidation. No pneumothorax.",
        },
        "visit_notes": [
            {"date": "2026-03-08", "note": "PAEDIATRIC URGENT. SpO2 89% on arrival. Audible wheeze. Intercostal retractions. PEFR 32% predicted. IV access failed ×2. IM Adrenaline 0.01 mg/kg prepared as standby. Paediatrician notified. PICU alert raised."},
            {"date": "2026-01-22", "note": "Routine review. Asthma well-controlled. No recent exacerbations. ICS compliance good."},
        ],
        "imaging": [
            {"date": "2026-03-08", "type": "X-Ray", "region": "Chest PA (Paediatric)", "report": "Bilateral hyperinflation with flattened hemidiaphragms. Increased AP diameter. Peribronchial thickening noted bilaterally. No focal consolidation. No pneumothorax. Heart size normal. No pleural effusion.", "impression": "Hyperinflated lungs consistent with acute severe asthma. No pneumothorax or consolidation. Repeat if clinical deterioration."},
            {"date": "2026-01-22", "type": "Spirometry", "region": "Pulmonary Function Test", "report": "FEV1: 92% predicted, FVC: 95% predicted, FEV1/FVC: 0.88. Post-bronchodilator: FEV1 improved to 98% predicted (+6%). Flow-volume loop normal shape.", "impression": "Normal spirometry with mild bronchodilator reversibility. Well-controlled asthma at baseline."},
        ],
    },
]


def get_session_state(session_id):
    """Get or create session state."""
    if session_id not in session_states:
        session_states[session_id] = {
            "last_seen_frame": None,
            "uploaded_images": [],
        }
    return session_states[session_id]


def get_active_client():
    """Get Gemini client using OAuth credentials."""
    if "oauth" not in session_credentials:
        return None
    creds_data = session_credentials["oauth"]
    creds = Credentials(token=creds_data["access_token"])
    return genai.Client(
        vertexai=True,
        project=creds_data["project_id"],
        location=creds_data["location"],
        credentials=creds,
    )


def get_system_prompt():
    """Build the system prompt based on current mode."""
    if current_mode == "patient":
        return _get_patient_prompt()
    return _get_nurse_prompt()


def _format_patient_self_context():
    """Return a plain-language patient record block for the patient-mode AI prompt."""
    if not logged_in_patient_id:
        return ""
    p = next((x for x in SAMPLE_PATIENTS if x["id"] == logged_in_patient_id), None)
    if not p:
        return ""

    # Medications with schedule description
    med_lines = []
    for m in p.get("medications", []):
        freq = m.get("frequency", "")
        med_lines.append(f"  • {m['name']} {m['dose']} — {freq}")
    med_str = "\n".join(med_lines) if med_lines else "  (none recorded)"

    # Diagnoses in patient-friendly form
    diag_str = "\n".join(f"  • {d}" for d in p.get("diagnoses", []))

    # Allergies
    allergy_str = ", ".join(p.get("allergies", ["None known"]))

    # Key vitals (latest only — no clinical detail)
    v = p.get("vitals", {})
    vital_lines = []
    if v.get("bp"):    vital_lines.append(f"Blood pressure: {v['bp']}")
    if v.get("hr"):    vital_lines.append(f"Heart rate: {v['hr']}")
    if v.get("spo2"):  vital_lines.append(f"Oxygen level: {v['spo2']}")
    if v.get("temp"):  vital_lines.append(f"Temperature: {v['temp']}")
    vital_str = "  " + " | ".join(vital_lines) if vital_lines else "  (not recorded)"

    # Dietary notes based on conditions — simple, safe tips the AI can expand on
    diet_hints = []
    diag_words = " ".join(p.get("diagnoses", [])).lower()
    if "diabetes" in diag_words:
        diet_hints.append("diabetes-friendly (low sugar, low refined carbs)")
    if "hypertension" in diag_words or "angina" in diag_words:
        diet_hints.append("heart-healthy (low sodium, low saturated fat)")
    if "arthritis" in diag_words:
        diet_hints.append("anti-inflammatory (omega-3 rich foods, avoid processed foods)")
    if "renal" in diag_words or "ckd" in diag_words or "kidney" in diag_words:
        diet_hints.append("kidney-friendly (low potassium, low phosphorus, limit protein)")
    if "asthma" in diag_words:
        diet_hints.append("avoid sulphite-containing foods (dried fruit, wine); maintain healthy weight")
    diet_hint_str = "; ".join(diet_hints) if diet_hints else "balanced, nutritious"

    # Last visit note summary
    notes = p.get("visit_notes", [])
    last_note = notes[0]["note"] if notes else "No recent visit notes."

    return f"""

══ YOUR PERSONAL HEALTH RECORD ══
Patient: {p['name']} (Date of Birth: {p['dob']}, Blood Type: {p['blood_type']})
Allergies: {allergy_str}

YOUR CONDITIONS:
{diag_str}

YOUR CURRENT MEDICATIONS:
{med_str}

RECENT VITALS (last measured):
{vital_str}

LAST VISIT NOTE:
  {last_note}

DIETARY GUIDANCE CONTEXT (use to help answer diet questions):
  Diet approach: {diet_hint_str}

══ END OF YOUR HEALTH RECORD ══
Use the above information to answer this patient's personal questions about their medicines, diet, conditions, and schedule. Always speak in plain, caring language — never use clinical jargon. Remind them to confirm anything important with their doctor."""


def _get_patient_prompt():
    name_section = (
        f"\nThe patient's name is: {user_name}. Greet them by name.\n"
        if user_name and user_name not in ("Nurse", "User")
        else ""
    )
    record_section = _format_patient_self_context()
    has_record = bool(record_section)

    opening = (
        f'Greet {user_name} warmly by name and let them know you have their health record loaded. Say: "Hi {user_name}! I have your health records here so I can give you personalised answers. What would you like to know — about your medicines, diet, schedule, or anything else?"'
        if has_record and user_name not in ("Nurse", "User", "")
        else 'Greet the patient warmly: "Hi there! I\'m MediSense, your AI health assistant. I\'m here to help you with questions about your medicines, prescriptions, or general health. What can I help you with today?"'
    )

    return f"""You are MediSense, a warm, friendly AI health companion speaking directly with a patient.

Your most important job is to LISTEN carefully to the patient, understand what they need, and give them clear, reassuring, plain-language guidance.
{name_section}
OPENING BEHAVIOUR (always do this at the start of a new session):
- {opening}
- After greeting, stay silent and LISTEN — wait for the patient to speak or show something.
- If the patient is quiet for a moment, gently prompt: "Take your time — just speak whenever you're ready."

WHAT YOU CAN HELP WITH:
- Answer questions about THIS patient's specific medications, doses, and schedule (if their record is loaded)
- Tell them what foods to eat or avoid given their conditions and medicines
- Explain when and how to take each of their medicines (with food, time of day, etc.)
- Confirm whether a medicine they see or hold up is theirs — check it against their record
- Explain what their diagnoses mean in simple language
- Identify medicines from photos of pills, blister packs, bottles, or packaging
- Read and explain prescriptions — drug name, dose, frequency, and instructions
- Describe common side effects and what to watch out for
- Explain drug interactions if the patient mentions other medicines
- Answer general health questions in simple, caring language
- Provide reassurance and emotional support for health worries
- Help the patient prepare questions to ask their doctor

HOW YOU LISTEN AND RESPOND:
- Always let the patient finish speaking before responding — never interrupt
- Acknowledge what they said first: "I hear you..." / "That makes sense..." / "Thanks for telling me that..."
- Use simple, warm, non-technical language — speak like a caring knowledgeable friend
- If something is unclear, ask one focused follow-up question at a time
- When answering diet questions: give specific, actionable food examples they can understand (e.g. "eat oatmeal, salmon, leafy greens — avoid salty snacks and processed food")
- When answering medication schedule questions: give a clear daily schedule (e.g. "Take Metformin with breakfast and dinner. Take Lisinopril every morning.")
- When checking a medicine: compare what the patient shows or describes with their medication list; confirm if it matches
- Always end with: "Does that make sense?" or "Is there anything else you'd like to know?"
- If you cannot clearly see a medicine or label: "Could you hold it a bit closer? I want to make sure I read it correctly."
- Speak naturally — the patient hears your responses via voice

SAFETY RULES (always follow):
1. You are an AI assistant, NOT a doctor or pharmacist. State this clearly for clinical decisions.
2. NEVER tell a patient to stop taking a prescribed medicine without consulting their doctor.
3. NEVER recommend a specific dose change — always say "as your doctor prescribed".
4. If a patient describes a severe allergic reaction (throat swelling, difficulty breathing, spreading rash) or overdose symptoms: calmly but clearly tell them to call emergency services (911) IMMEDIATELY.
5. For any question about changing their treatment, direct them to their prescribing doctor or pharmacist.
6. If a patient sounds distressed or in pain, acknowledge it with empathy before giving any information.

AVAILABLE TOOLS:
- log_clinical_note: Save a note about what the patient asked or identified — especially if they mention symptoms, medicines, or concerns worth flagging.
- flag_urgent: Raise a prominent urgent alert for serious safety concerns (e.g. potential overdose, severe interaction, allergic reaction symptoms).
{record_section}"""


def _format_active_patient():
    """Return a formatted patient-record block for the nurse system prompt, or empty string."""
    if not active_patient:
        return ""
    p = next((x for x in SAMPLE_PATIENTS if x["id"] == active_patient), None)
    if not p:
        return ""

    allergy_str = ", ".join(p["allergies"]) if p["allergies"] else "None known"
    diag_str    = "\n".join(f"  - {d}" for d in p["diagnoses"])
    med_str     = "\n".join(
        f"  - {m['name']} {m['dose']} ({m['frequency']})" for m in p["medications"]
    )
    vital_str   = "\n".join(f"  - {k.upper()}: {v}" for k, v in p["vitals"].items())

    # ESR trend
    esr_list = p["labs"].get("esr", [])
    if esr_list:
        esr_entries = "  " + " → ".join(
            f"{e['value']} mm/hr ({e['date']})" for e in esr_list
        )
        latest = esr_list[0]["value"]
        if len(esr_list) >= 2:
            trend = "RISING ↑" if latest > esr_list[1]["value"] else ("FALLING ↓" if latest < esr_list[1]["value"] else "STABLE →")
        else:
            trend = "single reading"
        esr_section = f"  ESR trend ({trend}): {esr_entries}\n  Reference: {esr_list[0]['ref']}"
    else:
        esr_section = "  ESR: Not available"

    other_labs = {k: v for k, v in p["labs"].items() if k != "esr"}
    other_lab_str = "\n".join(f"  - {k.upper()}: {v}" for k, v in other_labs.items())

    notes_str = "\n".join(
        f"  [{n['date']}] {n['note']}" for n in p["visit_notes"]
    )

    return f"""

\u2554{'=' * 66}\u2557
\u2551  ACTIVE PATIENT RECORD \u2014 LOADED FOR CLINICAL REVIEW
\u2560{'=' * 66}\u2563
\u2551  {p['name']}  |  Age {p['age']}  |  {p['gender']}  |  DOB {p['dob']}  |  Blood type {p['blood_type']}
\u255a{'=' * 66}\u255d

CHIEF COMPLAINT:  {p['chief_complaint']}

\u26a0\ufe0f  ALLERGIES:  {allergy_str}

DIAGNOSES:
{diag_str}

CURRENT MEDICATIONS:
{med_str}

CURRENT VITALS:
{vital_str}

LABORATORY RESULTS:
{esr_section}
{other_lab_str}

VISIT HISTORY:
{notes_str}

\u2550\u2550 END OF PATIENT RECORD \u2550\u2550
Refer to this record throughout the conversation. Alert on any finding that conflicts with medications, allergies, or current vitals.
"""


def _format_active_procedure():
    """Return a formatted procedure checklist block for the nurse system prompt."""
    if not active_procedure:
        return ""
    steps_str = ""
    for s in active_procedure["steps"]:
        status_icon = {"pending": "⬜", "verified": "✅", "warning": "⚠️", "flagged": "🚫"}.get(s["status"], "⬜")
        obs = f" — AI observation: {s['observation']}" if s.get("observation") else ""
        steps_str += f"  {status_icon} Step {s['step']}: {s['title']} — {s['detail']} [Visual check: {s['visual_check']}]{obs}\n"

    return f"""

══ ACTIVE PROCEDURE CHECKLIST: {active_procedure['name']} ══

{steps_str}
Guide the nurse through each pending step in order. Use the camera feed to visually verify
each step before marking it complete via the update_procedure_step tool.
══ END PROCEDURE ══
"""


def _get_nurse_prompt():
    name_section = (
        f"\nThe healthcare worker's name is: {user_name}.\n"
        if user_name and user_name != "Nurse"
        else ""
    )
    return f"""You are MediSense, an AI-powered remote emergency co-pilot designed for healthcare professionals in rural clinics, home-care settings, and resource-limited environments.

Your role is to assist junior nurses and caregivers performing complex procedures when expert physicians are not immediately available. You have full multimodal vision — you can see the live camera feed, screen shares, and uploaded images in real time.
{name_section}
CAPABILITIES YOU ACTIVELY USE:
- Camera feed: Analyze wounds, skin conditions, equipment states, patient appearance, device error codes
- Screen share: Read EHR data, vital monitor readings, lab results, imaging, equipment displays
- Voice and text: Respond to real-time clinical queries with immediate, actionable guidance
- Uploaded images: Analyze X-rays, lab reports, medication labels, medical device screenshots

HOW YOU RESPOND:
- Be calm, clear, and unambiguous in every response
- Number procedural steps clearly: "Step 1... Step 2..."
- Use "⚠️ URGENT:" prefix for any immediately concerning finding
- Use "✅" prefix for reassuring findings
- Always acknowledge what you can see visually when it is relevant
- Ask clarifying questions when critical information is missing or unclear
- Speak naturally — users will hear your responses via voice

CRITICAL SAFETY RULES (always follow these):
1. You are an AI ASSISTANT, NOT a physician. State this clearly for high-stakes decisions.
2. For any life-threatening emergency: ALWAYS direct users to call emergency services (911 or local equivalent) FIRST before anything else.
3. Drug dosage adjustments MUST be verified by a qualified physician before implementation — never recommend dose changes without this caveat.
4. When in doubt, escalate: recommend the user contact qualified medical staff immediately.
5. You do not diagnose conditions — you observe findings, suggest actions, and guide procedures.
6. If you cannot clearly see what the user is showing on camera, say so and ask them to reposition.

AVAILABLE TOOLS:
- log_clinical_note: Record an important observation, vital sign, or clinical finding to the session log. Use this proactively when you identify significant findings.
- flag_urgent: Raise a prominent urgent alert on the user's dashboard for immediately critical findings requiring fast action.
- update_procedure_step: When a procedure checklist is active, use this to verify or flag individual steps as the nurse performs them. Monitor the camera feed and mark steps as 'verified' when you see them done correctly, 'warning' if done with a minor concern, or 'flagged' if there is an issue needing correction.

PROCEDURE CHECKLIST MODE:
When a procedure checklist is active, you become a step-by-step guide:
1. Announce the current step clearly via voice: "Ready for Step N: [title]. [detail]"
2. Use the camera feed to verify each step visually — look for the visual check described
3. Call update_procedure_step to mark the step verified, or flag it with an observation
4. Only move to the next step after confirming the current one
5. If you see something wrong, call update_procedure_step with status 'flagged' and explain the issue
6. Provide encouraging confirmation: "Step N verified — well done. Moving to Step N+1..."
7. At the end, summarise all steps and any flagged items

SITUATIONAL AWARENESS:
Maintain full context of everything you have observed across the conversation. Reference prior findings when relevant (e.g., "Earlier I noted the redness around the wound — is it spreading?"). Track reported vitals, visible symptoms, and equipment states across the session.
{_format_active_procedure()}{_format_active_patient()}"""


class SessionBridge:
    """Bridge between Flask threads and the async Gemini Live session."""

    def __init__(self, loop):
        self.loop = loop
        self.queue = asyncio.Queue(maxsize=100)
        self.dropped_frames = 0

    def put_nowait(self, item):
        if not self.loop.is_closed():
            try:
                self.loop.call_soon_threadsafe(self._safe_put, item)
            except RuntimeError:
                pass

    def _safe_put(self, item):
        try:
            self.queue.put_nowait(item)
        except asyncio.QueueFull:
            self.dropped_frames += 1
            try:
                self.queue.get_nowait()
                self.queue.put_nowait(item)
            except Exception:
                pass


async def run_live_session(session_id, sid):
    """Run the Gemini Live API session with healthcare tools."""
    if session_id in live_sessions and live_sessions[session_id].get("active"):
        live_sessions[session_id]["sid"] = sid
        socketio.emit(
            "live_session_started",
            {"status": "reconnected", "user_name": user_name},
            room=sid,
        )
        return

    max_reconnects = 5
    reconnect_count = 0

    while reconnect_count < max_reconnects:
        resumption_handle = session_handles.get(session_id)
        input_queue = asyncio.Queue(maxsize=100)
        loop = asyncio.get_event_loop()
        bridge = SessionBridge(loop)
        bridge.queue = input_queue
        bridges[session_id] = bridge

        try:
            tools = [
                types.Tool(
                    function_declarations=[
                        types.FunctionDeclaration(
                            name="log_clinical_note",
                            description=(
                                "Record an important clinical observation, vital sign reading, "
                                "or finding to the session log. Use proactively when you "
                                "identify significant findings during the session."
                            ),
                            parameters={
                                "type": "object",
                                "properties": {
                                    "note": {
                                        "type": "string",
                                        "description": "The clinical note or observation to record",
                                    },
                                    "severity": {
                                        "type": "string",
                                        "enum": ["info", "warning", "urgent"],
                                        "description": "Severity level: info, warning, or urgent",
                                    },
                                },
                                "required": ["note"],
                            },
                        ),
                        types.FunctionDeclaration(
                            name="flag_urgent",
                            description=(
                                "Raise a prominent urgent alert on the user's dashboard "
                                "for an immediately critical finding that requires fast action."
                            ),
                            parameters={
                                "type": "object",
                                "properties": {
                                    "alert": {
                                        "type": "string",
                                        "description": "The urgent alert message",
                                    },
                                    "action_required": {
                                        "type": "string",
                                        "description": "The immediate action the user must take",
                                    },
                                },
                                "required": ["alert"],
                            },
                        ),
                        types.FunctionDeclaration(
                            name="generate_visual_aid",
                            description=(
                                "Generate a medical illustration, diagram, or visual aid image using AI image generation. "
                                "Use this when the user asks to see a diagram, when explaining anatomy, showing procedure technique, "
                                "illustrating a medical concept, or when a visual reference would help the user understand better. "
                                "Examples: anatomical diagrams, wound care illustrations, medication appearance, "
                                "injection angle guides, bandaging techniques, or any clinical visual aid."
                            ),
                            parameters={
                                "type": "object",
                                "properties": {
                                    "prompt": {
                                        "type": "string",
                                        "description": "Detailed description of the medical illustration to generate. Be specific about anatomy, angles, labels, colors, and style. Always specify 'medical illustration style, clean, professional, labeled diagram'.",
                                    },
                                    "context": {
                                        "type": "string",
                                        "description": "Brief clinical context for why this visual is needed",
                                    },
                                },
                                "required": ["prompt"],
                            },
                        ),
                        types.FunctionDeclaration(
                            name="update_procedure_step",
                            description=(
                                "Update the status of a step in the active procedure checklist. "
                                "Use this when you visually verify a step is completed correctly "
                                "via the camera, or when you detect an issue with a step. "
                                "Call this proactively as you observe the nurse performing steps."
                            ),
                            parameters={
                                "type": "object",
                                "properties": {
                                    "step_number": {
                                        "type": "integer",
                                        "description": "The procedure step number (1-based)",
                                    },
                                    "status": {
                                        "type": "string",
                                        "enum": ["verified", "warning", "flagged"],
                                        "description": "verified = step completed correctly; warning = step done but with a concern; flagged = issue detected that needs correction",
                                    },
                                    "observation": {
                                        "type": "string",
                                        "description": "What the AI observed about this step (camera/screen finding)",
                                    },
                                },
                                "required": ["step_number", "status", "observation"],
                            },
                        ),
                    ]
                )
            ]

            config = types.LiveConnectConfig(
                response_modalities=["AUDIO"],
                system_instruction=get_system_prompt(),
                media_resolution=types.MediaResolution.MEDIA_RESOLUTION_MEDIUM,
                context_window_compression=types.ContextWindowCompressionConfig(
                    trigger_tokens=100000,
                    sliding_window=types.SlidingWindow(target_tokens=80000),
                ),
                session_resumption=types.SessionResumptionConfig(
                    handle=resumption_handle
                ),
                speech_config=types.SpeechConfig(
                    voice_config=types.VoiceConfig(
                        prebuilt_voice_config=types.PrebuiltVoiceConfig(
                            voice_name="Aoede"
                        )
                    )
                ),
                tools=tools,
            )

            client = get_active_client()
            if client is None:
                current_sid = starting_session_sids.get(session_id, sid)
                socketio.emit(
                    "live_session_error",
                    {
                        "error": "Authentication required. Please enter your Project ID and access token.",
                        "code": 401,
                    },
                    room=current_sid,
                )
                starting_sessions.discard(session_id)
                return

            async with client.aio.live.connect(
                model=GEMINI_LIVE_MODEL, config=config
            ) as session:
                current_sid = starting_session_sids.get(session_id, sid)
                live_sessions[session_id] = {"active": True, "sid": current_sid}
                starting_sessions.discard(session_id)
                starting_session_sids.pop(session_id, None)
                socketio.emit(
                    "live_session_started",
                    {"status": "connected", "user_name": user_name},
                    room=current_sid,
                )

                async def sender_loop():
                    while live_sessions.get(session_id, {}).get("active"):
                        try:
                            item = await asyncio.wait_for(input_queue.get(), timeout=0.5)

                            if item["type"] == "audio":
                                await session.send_realtime_input(audio=item["data"])
                            elif item["type"] == "video":
                                await session.send_realtime_input(video=item["data"])
                            elif item["type"] == "text":
                                await session.send_client_content(
                                    turns=types.Content(
                                        role="user",
                                        parts=[types.Part(text=item["data"])],
                                    ),
                                    turn_complete=True,
                                )
                            elif item["type"] == "image_with_text":
                                for img_data in item.get("images", []):
                                    if isinstance(img_data, dict):
                                        blob = types.Blob(
                                            mime_type=img_data.get("mime_type", "image/jpeg"),
                                            data=img_data["data"],
                                        )
                                        for _ in range(5):
                                            await session.send_realtime_input(video=blob)
                                            await asyncio.sleep(0.1)
                                await session.send_client_content(
                                    turns=types.Content(
                                        role="user",
                                        parts=[types.Part(text=item.get("text", ""))],
                                    ),
                                    turn_complete=True,
                                )
                            elif item["type"] == "image":
                                for img_data in item["data"]:
                                    if isinstance(img_data, dict):
                                        blob = types.Blob(
                                            mime_type=img_data.get("mime_type", "image/jpeg"),
                                            data=img_data["data"],
                                        )
                                        for _ in range(3):
                                            await session.send_realtime_input(video=blob)
                                            await asyncio.sleep(0.1)
                            elif item["type"] == "tool_response":
                                await session.send_tool_response(
                                    function_responses=item["data"]
                                )
                            input_queue.task_done()
                        except asyncio.TimeoutError:
                            continue
                        except Exception as e:
                            logging.error(f"Send error: {e}")

                async def receiver_loop():
                    try:
                        while live_sessions.get(session_id, {}).get("active"):
                            async for response in session.receive():
                                if not live_sessions.get(session_id, {}).get("active"):
                                    return "ended"
                                current_sid = live_sessions[session_id]["sid"]

                                if response.session_resumption_update:
                                    update = response.session_resumption_update
                                    if update.resumable and update.new_handle:
                                        session_handles[session_id] = update.new_handle
                                        logging.info(f"Session {session_id}: captured resumption handle")

                                if response.tool_call:
                                    for fc in response.tool_call.function_calls:
                                        socketio.emit(
                                            "tool_call",
                                            {
                                                "function_name": fc.name,
                                                "function_args": dict(fc.args),
                                                "function_call_id": fc.id,
                                            },
                                            room=current_sid,
                                        )

                                if (
                                    response.server_content
                                    and response.server_content.model_turn
                                ):
                                    for part in response.server_content.model_turn.parts:
                                        if part.text:
                                            socketio.emit(
                                                "text_response",
                                                {"text": part.text},
                                                room=current_sid,
                                            )
                                        if part.inline_data:
                                            audio_b64 = base64.b64encode(
                                                part.inline_data.data
                                            ).decode("utf-8")
                                            socketio.emit(
                                                "audio_response",
                                                {
                                                    "audio": audio_b64,
                                                    "mime_type": part.inline_data.mime_type,
                                                },
                                                room=current_sid,
                                            )
                    except asyncio.CancelledError:
                        return "cancelled"
                    except Exception as e:
                        error_msg = str(e)
                        if "1011" in error_msg or "Insufficient model resources" in error_msg:
                            socketio.emit(
                                "live_session_error",
                                {"error": "Server overloaded. Please try again.", "code": 1011},
                                room=live_sessions[session_id]["sid"],
                            )
                            return "capacity_error"
                        if "1000" in error_msg or "cancelled" in error_msg.lower():
                            return "reconnect"
                        logging.error(f"Receive error: {e}")
                        return "error"
                    return "ended"

                sender_task = asyncio.create_task(sender_loop())
                receiver_task = asyncio.create_task(receiver_loop())

                done, pending = await asyncio.wait(
                    [sender_task, receiver_task], return_when=asyncio.FIRST_COMPLETED
                )

                session_active = live_sessions.get(session_id, {}).get("active", False)
                should_reconnect = False
                for task in done:
                    try:
                        result = task.result()
                        if result in ("reconnect", "ended") and session_active:
                            should_reconnect = True
                    except Exception:
                        pass

                for task in pending:
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass

                get_session_state(session_id)["should_reconnect"] = should_reconnect

            session_active = live_sessions.get(session_id, {}).get("active", False)
            should_reconnect = get_session_state(session_id).get("should_reconnect", False)
            has_handle = session_id in session_handles

            if session_active and (should_reconnect or has_handle):
                reconnect_count += 1
                logging.info(f"Reconnecting session {session_id} (attempt {reconnect_count})")
                await asyncio.sleep(1)
                continue
            else:
                break

        except Exception as e:
            logging.error(f"Session error: {e}")
            current_sid = live_sessions.get(session_id, {}).get("sid", sid)
            error_msg = str(e)

            if "1011" in error_msg or "Insufficient model resources" in error_msg:
                socketio.emit(
                    "live_session_error",
                    {"error": "Server overloaded. Please try again.", "code": 1011},
                    room=current_sid,
                )
                break

            socketio.emit("live_session_error", {"error": str(e)}, room=current_sid)

            if ("1000" in error_msg or "cancelled" in error_msg.lower()) and reconnect_count < max_reconnects:
                reconnect_count += 1
                await asyncio.sleep(2)
                continue
            else:
                break

    if session_id in bridges:
        del bridges[session_id]
    if session_id in live_sessions:
        final_sid = live_sessions[session_id]["sid"]
        del live_sessions[session_id]
        socketio.emit(
            "session_ended_reconnect",
            {"session_id": session_id, "can_resume": session_id in session_handles},
            room=final_sid,
        )
    starting_sessions.discard(session_id)


def start_background_loop(session_id, sid):
    """Start the async event loop for the Gemini Live session in a thread."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(run_live_session(session_id, sid))
    except Exception as e:
        logging.error(f"Loop error: {e}")
    finally:
        starting_sessions.discard(session_id)
        loop.close()


# ── Socket handlers ─────────────────────────────────────────────────────────

@socketio.on("connect")
def handle_connect():
    logging.info(f"Client connected: {request.sid}")


@socketio.on("disconnect")
def handle_disconnect():
    logging.info(f"Client disconnected: {request.sid}")


@socketio.on("start_live_session")
def handle_start(data):
    session_id = data.get("session_id", "default")
    sid = request.sid
    get_session_state(session_id)["uploaded_images"] = []
    get_session_state(session_id)["last_seen_frame"] = None

    if session_id in bridges and session_id in live_sessions:
        live_sessions[session_id]["sid"] = sid
        emit("live_session_started", {"status": "reconnected", "user_name": user_name})
        return

    if session_id in starting_sessions:
        starting_session_sids[session_id] = sid
        return

    starting_sessions.add(session_id)
    starting_session_sids[session_id] = sid
    t = threading.Thread(target=start_background_loop, args=(session_id, sid), daemon=True)
    t.start()


@socketio.on("stop_live_session")
def handle_stop(data):
    session_id = data.get("session_id")
    if session_id in live_sessions:
        live_sessions[session_id]["active"] = False
        emit("live_session_stopped")


@socketio.on("check_session_status")
def handle_check_session(data):
    session_id = data.get("session_id")
    sid = request.sid
    if session_id in bridges and session_id in live_sessions:
        live_sessions[session_id]["sid"] = sid
        emit("live_session_started", {"status": "reconnected", "user_name": user_name})
        return {"active": True}
    elif session_id in starting_sessions:
        starting_session_sids[session_id] = sid
        return {"active": False, "starting": True}
    return {"active": False}


@socketio.on("send_audio")
def handle_audio(data):
    session_id = data.get("session_id")
    audio = data.get("audio")
    if not session_id or session_id not in bridges or not audio:
        return
    try:
        import struct
        if isinstance(audio, list):
            b = struct.pack(f"<{len(audio)}h", *audio)
        else:
            b = base64.b64decode(audio)
        audio_blob = types.Blob(mime_type="audio/pcm;rate=16000", data=b)
        bridges[session_id].put_nowait({"type": "audio", "data": audio_blob})
    except Exception as e:
        logging.error(f"Audio error: {e}")


@socketio.on("send_camera_frame")
def handle_video(data):
    session_id = data.get("session_id")
    frame = data.get("frame")
    if session_id and frame:
        get_session_state(session_id)["last_seen_frame"] = f"data:image/jpeg;base64,{frame}"
    if session_id in bridges and frame:
        try:
            frame_bytes = base64.b64decode(frame)
            video_blob = types.Blob(mime_type="image/jpeg", data=frame_bytes)
            bridges[session_id].put_nowait({"type": "video", "data": video_blob})
        except Exception as e:
            logging.error(f"Frame error: {e}")


@socketio.on("send_text_message")
def handle_text(data):
    session_id = data.get("session_id")
    text = data.get("text")
    if session_id in bridges and text:
        bridges[session_id].put_nowait({"type": "text", "data": text})


@socketio.on("send_uploaded_images")
def handle_uploaded_images(data):
    session_id = data.get("session_id")
    images = data.get("images", [])
    if not session_id or not images:
        return {"status": "error", "message": "No session_id or images"}

    session_state = get_session_state(session_id)
    session_state["uploaded_images"] = images
    if images:
        session_state["last_seen_frame"] = images[-1]

    if session_id in bridges and session_id in live_sessions:
        try:
            processed_images = []
            for img_data_url in images:
                if isinstance(img_data_url, str) and "," in img_data_url:
                    header, b64_data = img_data_url.split(",", 1)
                    img_bytes = base64.b64decode(b64_data)
                    if PIL_AVAILABLE:
                        img = Image.open(pil_io.BytesIO(img_bytes))
                        if img.mode in ("RGBA", "P"):
                            img = img.convert("RGB")
                        img.thumbnail((768, 768), Image.Resampling.LANCZOS)
                        canvas = Image.new("RGB", (768, 768), (128, 128, 128))
                        x = (768 - img.width) // 2
                        y = (768 - img.height) // 2
                        canvas.paste(img, (x, y))
                        buf = pil_io.BytesIO()
                        canvas.save(buf, format="JPEG", quality=85)
                        processed_images.append({"mime_type": "image/jpeg", "data": buf.getvalue()})
                    else:
                        mime_type = "image/jpeg" if "jpeg" in header else "image/png"
                        processed_images.append({"mime_type": mime_type, "data": img_bytes})

            if processed_images:
                bridges[session_id].put_nowait({"type": "image", "data": processed_images})
                return {"status": "ok", "queued": len(processed_images)}
        except Exception as e:
            logging.error(f"Image upload error: {e}")
            return {"status": "error", "message": str(e)}
    return {"status": "stored"}


@socketio.on("send_message_with_images")
def handle_message_with_images(data):
    session_id = data.get("session_id")
    text = data.get("text", "")
    images = data.get("images", [])

    if not session_id or session_id not in bridges:
        return {"status": "error", "message": "Session not active"}

    session_state = get_session_state(session_id)
    session_state["uploaded_images"] = images
    if images:
        session_state["last_seen_frame"] = images[-1]

    try:
        processed_images = []
        for img_data_url in images:
            if isinstance(img_data_url, str) and "," in img_data_url:
                header, b64_data = img_data_url.split(",", 1)
                img_bytes = base64.b64decode(b64_data)
                mime_type = "image/jpeg" if "jpeg" in header else "image/png"
                if PIL_AVAILABLE:
                    img = Image.open(pil_io.BytesIO(img_bytes))
                    if img.mode in ("RGBA", "P"):
                        img = img.convert("RGB")
                    img.thumbnail((768, 768), Image.Resampling.LANCZOS)
                    canvas = Image.new("RGB", (768, 768), (128, 128, 128))
                    x = (768 - img.width) // 2
                    y = (768 - img.height) // 2
                    canvas.paste(img, (x, y))
                    buf = pil_io.BytesIO()
                    canvas.save(buf, format="JPEG", quality=85)
                    processed_images.append({"mime_type": "image/jpeg", "data": buf.getvalue()})
                else:
                    processed_images.append({"mime_type": mime_type, "data": img_bytes})

        context = f"[User uploaded {len(images)} clinical image(s)]: {text}" if images else text
        bridges[session_id].put_nowait(
            {"type": "image_with_text", "images": processed_images, "text": context}
        )
        return {"status": "ok"}
    except Exception as e:
        logging.error(f"Message with images error: {e}")
        return {"status": "error", "message": str(e)}


# ── REST API routes ──────────────────────────────────────────────────────────

@app.route("/api/tool-call", methods=["POST"])
def handle_tool_call():
    """Handle tool calls forwarded from the frontend (log_clinical_note, flag_urgent)."""
    try:
        data = request.json
        session_id = data.get("session_id", "default")
        function_name = data.get("function_name")
        function_args = data.get("function_args", {})
        function_call_id = data.get("function_call_id")

        if function_name == "log_clinical_note":
            note = function_args.get("note", "")
            severity = function_args.get("severity", "info")
            entry = {
                "session_id": session_id,
                "timestamp": datetime.now().isoformat(),
                "note": note,
                "severity": severity,
                "type": "note",
            }
            clinical_notes.append(entry)

            if session_id in bridges and function_call_id:
                response = [
                    types.FunctionResponse(
                        id=function_call_id,
                        name="log_clinical_note",
                        response={"status": "logged", "note_id": len(clinical_notes)},
                    )
                ]
                bridges[session_id].put_nowait({"type": "tool_response", "data": response})

            if session_id in live_sessions:
                socketio.emit(
                    "clinical_note_added", entry,
                    room=live_sessions[session_id]["sid"],
                )
            return jsonify({"status": "logged", "entry": entry})

        elif function_name == "flag_urgent":
            alert = function_args.get("alert", "")
            action = function_args.get("action_required", "")
            entry = {
                "session_id": session_id,
                "timestamp": datetime.now().isoformat(),
                "alert": alert,
                "action_required": action,
                "severity": "urgent",
                "type": "urgent",
            }
            clinical_notes.append(entry)

            if session_id in bridges and function_call_id:
                response = [
                    types.FunctionResponse(
                        id=function_call_id,
                        name="flag_urgent",
                        response={"status": "flagged"},
                    )
                ]
                bridges[session_id].put_nowait({"type": "tool_response", "data": response})

            if session_id in live_sessions:
                socketio.emit(
                    "urgent_alert", entry,
                    room=live_sessions[session_id]["sid"],
                )
            return jsonify({"status": "flagged", "entry": entry})

        elif function_name == "update_procedure_step":
            global active_procedure
            step_number = function_args.get("step_number", 0)
            status = function_args.get("status", "verified")
            observation = function_args.get("observation", "")

            result_msg = "no_active_procedure"
            if active_procedure:
                for s in active_procedure.get("steps", []):
                    if s["step"] == step_number:
                        s["status"] = status
                        s["observation"] = observation
                        result_msg = "updated"
                        break

            if session_id in bridges and function_call_id:
                response = [
                    types.FunctionResponse(
                        id=function_call_id,
                        name="update_procedure_step",
                        response={"status": result_msg, "step": step_number},
                    )
                ]
                bridges[session_id].put_nowait({"type": "tool_response", "data": response})

            if session_id in live_sessions:
                socketio.emit(
                    "procedure_step_update",
                    {
                        "step_number": step_number,
                        "status": status,
                        "observation": observation,
                        "timestamp": datetime.now().isoformat(),
                    },
                    room=live_sessions[session_id]["sid"],
                )

            # Also log as a clinical note
            severity = "warning" if status == "flagged" else "info"
            note_entry = {
                "session_id": session_id,
                "timestamp": datetime.now().isoformat(),
                "note": f"[Procedure Step {step_number}] {observation}",
                "severity": severity,
                "type": "note",
            }
            clinical_notes.append(note_entry)
            if session_id in live_sessions:
                socketio.emit("clinical_note_added", note_entry, room=live_sessions[session_id]["sid"])

            return jsonify({"status": result_msg, "step": step_number})

        elif function_name == "generate_visual_aid":
            prompt = function_args.get("prompt", "")
            context = function_args.get("context", "")

            # Call Nano Banana image generation
            image_b64 = None
            try:
                client = get_active_client()
                if client:
                    img_response = client.models.generate_content(
                        model=GEMINI_IMAGE_MODEL,
                        contents=f"Generate a clear, professional medical illustration: {prompt}",
                        config=types.GenerateContentConfig(
                            response_modalities=["TEXT", "IMAGE"],
                        ),
                    )
                    for part in img_response.candidates[0].content.parts:
                        if part.inline_data and part.inline_data.mime_type.startswith("image/"):
                            image_b64 = base64.b64encode(part.inline_data.data).decode("utf-8")
                            mime_type = part.inline_data.mime_type
                            break
            except Exception as img_err:
                logging.error(f"Image generation error: {img_err}")

            if session_id in bridges and function_call_id:
                response = [
                    types.FunctionResponse(
                        id=function_call_id,
                        name="generate_visual_aid",
                        response={"status": "generated" if image_b64 else "failed"},
                    )
                ]
                bridges[session_id].put_nowait({"type": "tool_response", "data": response})

            if image_b64 and session_id in live_sessions:
                socketio.emit(
                    "image_response",
                    {
                        "image": image_b64,
                        "mime_type": mime_type,
                        "context": context or "Visual aid",
                    },
                    room=live_sessions[session_id]["sid"],
                )

            return jsonify({"status": "generated" if image_b64 else "failed"})

        return jsonify({"error": "Unknown tool"}), 400

    except Exception as e:
        logging.error(f"Tool call error: {e}")
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/api/generate-image", methods=["POST"])
def generate_image():
    """Generate a medical illustration using Nano Banana (Gemini image generation)."""
    try:
        client = get_active_client()
        if not client:
            return jsonify({"error": "Not authenticated"}), 401

        data = request.json
        prompt = data.get("prompt", "")
        if not prompt:
            return jsonify({"error": "Prompt is required"}), 400

        response = client.models.generate_content(
            model=GEMINI_IMAGE_MODEL,
            contents=f"Generate a clear, professional medical illustration: {prompt}",
            config=types.GenerateContentConfig(
                response_modalities=["TEXT", "IMAGE"],
            ),
        )

        image_b64 = None
        mime_type = "image/png"
        text_caption = ""
        for part in response.candidates[0].content.parts:
            if part.inline_data and part.inline_data.mime_type.startswith("image/"):
                image_b64 = base64.b64encode(part.inline_data.data).decode("utf-8")
                mime_type = part.inline_data.mime_type
            elif part.text:
                text_caption = part.text

        if not image_b64:
            return jsonify({"error": "No image generated"}), 500

        return jsonify({
            "image": image_b64,
            "mime_type": mime_type,
            "caption": text_caption,
        })

    except Exception as e:
        logging.error(f"Image generation error: {e}")
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/api/validate-token", methods=["POST"])
def validate_token():
    global session_credentials
    data = request.json
    access_token = data.get("accessToken")
    location = data.get("location", DEFAULT_LOCATION_ID)
    project_id = data.get("projectId", "").strip() or DEFAULT_PROJECT_ID

    if not access_token:
        return jsonify({"valid": False, "message": "Access token required"})

    try:
        creds = Credentials(token=access_token)
        test_client = genai.Client(
            vertexai=True, project=project_id, location=location, credentials=creds
        )
        test_client.models.generate_content(
            model="gemini-2.0-flash", contents="Say 'ok' and nothing else"
        )
        session_credentials["oauth"] = {
            "credentials": creds,
            "project_id": project_id,
            "location": location,
            "access_token": access_token,
        }
        logging.info(f"User authenticated with project: {project_id}")
        return jsonify({"valid": True, "project": project_id})
    except Exception as e:
        logging.error(f"Token validation failed: {e}")
        return jsonify({"valid": False, "message": str(e)})


@app.route("/api/auth-status", methods=["GET"])
def auth_status():
    if "oauth" in session_credentials:
        return jsonify(
            {"authenticated": True, "project": session_credentials["oauth"]["project_id"]}
        )
    return jsonify({"authenticated": False, "project": DEFAULT_PROJECT_ID})


@app.route("/api/set-mode", methods=["POST"])
def set_mode_route():
    global current_mode, user_name
    mode = request.json.get("mode", "nurse")
    if mode not in ("nurse", "patient"):
        return jsonify({"success": False, "message": "Invalid mode"}), 400
    current_mode = mode
    # Reset default name when switching modes so the prompt is contextually correct
    if mode == "patient" and user_name == "Nurse":
        user_name = "User"
    elif mode == "nurse" and user_name == "User":
        user_name = "Nurse"
    logging.info(f"Mode switched to: {current_mode}")
    return jsonify({"success": True, "mode": current_mode})


@app.route("/api/get-mode", methods=["GET"])
def get_mode_route():
    return jsonify({"mode": current_mode})


@app.route("/api/patients", methods=["GET"])
def get_patients_route():
    """Return the sample patient list (lightweight summary for UI)."""
    summary = []
    for p in SAMPLE_PATIENTS:
        esr_list = p["labs"].get("esr", [])
        esr_latest = esr_list[0] if esr_list else None
        summary.append({
            "id": p["id"],
            "name": p["name"],
            "age": p["age"],
            "gender": p["gender"],
            "blood_type": p["blood_type"],
            "chief_complaint": p["chief_complaint"],
            "diagnoses": p["diagnoses"],
            "allergies": p["allergies"],
            "esr_latest": esr_latest,
            "esr_history": esr_list,
            "vitals": p["vitals"],
            "medications": p["medications"],
            "labs_summary": {k: v for k, v in p["labs"].items() if k != "esr"},
            "visit_notes": p["visit_notes"],
            "imaging": p.get("imaging", []),
            "is_active": active_patient == p["id"],
        })
    return jsonify({"patients": summary})


@app.route("/api/set-patient", methods=["POST"])
def set_patient_route():
    global active_patient
    patient_id = request.json.get("patient_id")  # None to clear
    if patient_id is not None:
        ids = [p["id"] for p in SAMPLE_PATIENTS]
        if patient_id not in ids:
            return jsonify({"success": False, "message": "Patient not found"}), 404
    active_patient = patient_id
    logging.info(f"Active patient set: {active_patient}")
    return jsonify({"success": True, "patient_id": active_patient})


@app.route("/api/patient-login", methods=["POST"])
def patient_login_route():
    global logged_in_patient_id, user_name
    patient_id = request.json.get("patient_id")
    if not patient_id:
        return jsonify({"success": False, "message": "patient_id required"}), 400
    p = next((x for x in SAMPLE_PATIENTS if x["id"] == patient_id), None)
    if not p:
        return jsonify({"success": False, "message": "Patient not found"}), 404
    logged_in_patient_id = patient_id
    user_name = p["name"].split()[0]   # use first name
    logging.info(f"Patient logged in: {logged_in_patient_id} ({user_name})")
    return jsonify({
        "success": True,
        "patient_id": patient_id,
        "name": p["name"],
        "first_name": user_name,
    })


@app.route("/api/patient-logout", methods=["POST"])
def patient_logout_route():
    global logged_in_patient_id, user_name
    logged_in_patient_id = None
    user_name = "Nurse"
    logging.info("Patient logged out")
    return jsonify({"success": True})


@app.route("/api/set-user-name", methods=["POST"])
def set_user_name_route():
    global user_name
    user_name = request.json.get("name", "Nurse")
    return jsonify({"success": True, "name": user_name})


@app.route("/api/get-user-name", methods=["GET"])
def get_user_name_route():
    return jsonify({"name": user_name})


@app.route("/api/clinical-notes", methods=["GET"])
def get_clinical_notes():
    session_id = request.args.get("session_id", "default")
    notes = [n for n in clinical_notes if n.get("session_id") == session_id]
    return jsonify({"notes": notes})


@app.route("/api/clear-session", methods=["POST"])
def clear_session():
    global clinical_notes
    session_id = request.json.get("session_id", "default")
    if session_id in session_states:
        del session_states[session_id]
    if session_id in session_handles:
        del session_handles[session_id]
    clinical_notes = [n for n in clinical_notes if n.get("session_id") != session_id]
    return jsonify({"success": True})


@app.route("/api/procedures", methods=["GET"])
def get_procedures():
    """Return the procedure checklist library."""
    return jsonify({"procedures": PROCEDURE_LIBRARY})


@app.route("/api/start-procedure", methods=["POST"])
def start_procedure():
    """Activate a procedure checklist by ID."""
    global active_procedure
    procedure_id = request.json.get("procedure_id")
    proc = next((p for p in PROCEDURE_LIBRARY if p["id"] == procedure_id), None)
    if not proc:
        return jsonify({"success": False, "message": "Procedure not found"}), 404

    import copy
    active_procedure = copy.deepcopy(proc)
    for s in active_procedure["steps"]:
        s["status"] = "pending"
        s["observation"] = ""
    logging.info(f"Procedure started: {active_procedure['name']}")
    return jsonify({"success": True, "procedure": active_procedure})


@app.route("/api/stop-procedure", methods=["POST"])
def stop_procedure():
    """Deactivate the current procedure checklist."""
    global active_procedure
    active_procedure = None
    return jsonify({"success": True})


@app.route("/api/active-procedure", methods=["GET"])
def get_active_procedure():
    """Return the currently active procedure checklist."""
    return jsonify({"procedure": active_procedure})


@app.route("/api/generate-sbar", methods=["POST"])
def generate_sbar():
    """Generate an SBAR handover note using Gemini."""
    try:
        client = get_active_client()
        if not client:
            return jsonify({"error": "Not authenticated"}), 401

        data = request.json
        patient_id = data.get("patient_id")
        session_notes = data.get("clinical_notes", [])

        patient = None
        if patient_id:
            patient = next((p for p in SAMPLE_PATIENTS if p["id"] == patient_id), None)

        context_parts = []
        if patient:
            v = patient.get("vitals", {})
            vitals_str = ", ".join(f"{k.upper()}: {val}" for k, val in v.items())
            esr_list = patient["labs"].get("esr", [])
            esr_str = " → ".join(f"{e['value']} ({e['date']})" for e in esr_list) if esr_list else "N/A"
            labs_other = {k: val for k, val in patient["labs"].items() if k != "esr"}
            labs_str = "\n".join(f"  {k.upper()}: {val}" for k, val in labs_other.items())
            med_str = "\n".join(
                f"  - {m['name']} {m['dose']} ({m['frequency']})" for m in patient["medications"]
            )
            last_note = patient["visit_notes"][0]["note"] if patient["visit_notes"] else "No recent notes."
            context_parts.append(
                f"Patient: {patient['name']}, {patient['age']}yo {patient['gender']}, Blood type: {patient['blood_type']}\n"
                f"Chief Complaint: {patient['chief_complaint']}\n"
                f"Diagnoses: {', '.join(patient['diagnoses'])}\n"
                f"Allergies: {', '.join(patient['allergies'])}\n"
                f"Current Medications:\n{med_str}\n"
                f"Current Vitals: {vitals_str}\n"
                f"ESR Trend: {esr_str}\n"
                f"Key Labs:\n{labs_str}\n"
                f"Most Recent Visit Note: {last_note}"
            )

        if session_notes:
            notes_text = "\n".join(
                f"  [{n.get('timestamp', '')[:16]}] {n.get('note') or n.get('alert', '')} ({n.get('severity', '')})"
                for n in session_notes
            )
            context_parts.append(f"Session Clinical Notes (this shift):\n{notes_text}")

        if not context_parts:
            return jsonify({"error": "No patient context available"}), 400

        full_context = "\n\n".join(context_parts)
        prompt = f"""Generate a professional clinical SBAR handover note for the following patient.

Use this exact structure with bold section headers:

**SITUATION**
What is happening right now — current status and primary concern requiring handover.

**BACKGROUND**
Relevant medical history, current diagnoses, active medications, allergies, and reason for admission.

**ASSESSMENT**
Current clinical findings: vital signs, lab results, observed changes, risk indicators.

**RECOMMENDATION**
Specific actions required by the incoming team — monitoring needs, pending results, escalation triggers.

Patient Data:
{full_context}

Write in professional clinical nursing language. Be specific and actionable. Keep each section to 2-4 concise sentences."""

        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=prompt,
        )
        return jsonify({"sbar": response.text, "generated_at": datetime.now().isoformat()})

    except Exception as e:
        logging.error(f"SBAR generation error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/generate-ddx", methods=["POST"])
def generate_ddx():
    """Generate AI-powered differential diagnoses using Gemini."""
    import json as json_lib
    try:
        client = get_active_client()
        if not client:
            return jsonify({"error": "Not authenticated"}), 401

        data = request.json
        patient_id = data.get("patient_id")
        session_context = data.get("session_context", "")

        patient = None
        if patient_id:
            patient = next((p for p in SAMPLE_PATIENTS if p["id"] == patient_id), None)
        if not patient:
            return jsonify({"error": "Patient not found"}), 404

        v = patient.get("vitals", {})
        vitals_str = ", ".join(f"{k.upper()}: {val}" for k, val in v.items())
        esr_list = patient["labs"].get("esr", [])
        esr_str = " → ".join(f"{e['value']} mm/hr ({e['date']})" for e in esr_list) if esr_list else ""
        labs_other = {k: val for k, val in patient["labs"].items() if k != "esr"}
        labs_str = "\n".join(f"{k.upper()}: {val}" for k, val in labs_other.items())

        prompt = f"""You are a clinical decision support AI. Analyze this patient and provide structured differential diagnoses.

Patient: {patient['name']}, {patient['age']}yo {patient['gender']}
Chief Complaint: {patient['chief_complaint']}
Existing Diagnoses on File: {', '.join(patient['diagnoses'])}
Vitals: {vitals_str}
Key Labs:
{labs_str}
{f"ESR Trend: {esr_str}" if esr_str else ""}
Medications: {', '.join(f"{m['name']} {m['dose']}" for m in patient['medications'])}
{f"Additional session context: {session_context}" if session_context else ""}

Respond ONLY with valid JSON (no markdown, no explanation):
{{
  "differentials": [
    {{"rank": 1, "diagnosis": "Primary Diagnosis", "likelihood": "High", "key_evidence": "2-3 key findings", "next_step": "Most urgent action"}},
    {{"rank": 2, "diagnosis": "Second Differential", "likelihood": "Medium", "key_evidence": "Supporting evidence", "next_step": "Recommended action"}},
    {{"rank": 3, "diagnosis": "Third Differential", "likelihood": "Medium", "key_evidence": "Supporting evidence", "next_step": "Recommended action"}},
    {{"rank": 4, "diagnosis": "Fourth Differential", "likelihood": "Low", "key_evidence": "Supporting evidence", "next_step": "Recommended action"}}
  ],
  "red_flags": ["Critical finding 1", "Critical finding 2"],
  "immediate_priority": "Single most urgent action right now"
}}"""

        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=prompt,
        )
        text = response.text.strip()
        text = re.sub(r'^```(?:json)?\s*', '', text)
        text = re.sub(r'\s*```$', '', text)
        result = json_lib.loads(text)
        return jsonify(result)

    except Exception as e:
        logging.error(f"DDx generation error: {e}")
        return jsonify({"error": str(e)}), 500


def _generate_imaging_svg(img_type, region, findings=""):
    """Generate an inline SVG visualization for a medical imaging study."""
    if img_type == "X-Ray" and "Hand" in region:
        return '''<svg viewBox="0 0 400 350" xmlns="http://www.w3.org/2000/svg" style="width:100%;max-width:400px;border-radius:8px">
  <rect width="400" height="350" fill="#0a0a14"/>
  <text x="10" y="18" fill="#4a6" font-size="10" font-family="monospace">X-RAY BILATERAL HANDS AP</text>
  <text x="280" y="18" fill="#888" font-size="9" font-family="monospace">MediSense PACS</text>
  <!-- Left hand -->
  <g opacity="0.85">
    <line x1="90" y1="280" x2="90" y2="180" stroke="#c8c8d8" stroke-width="10" stroke-linecap="round"/>
    <line x1="70" y1="280" x2="55" y2="180" stroke="#c8c8d8" stroke-width="8" stroke-linecap="round"/>
    <line x1="110" y1="278" x2="120" y2="175" stroke="#c8c8d8" stroke-width="8" stroke-linecap="round"/>
    <line x1="50" y1="275" x2="30" y2="195" stroke="#c8c8d8" stroke-width="7" stroke-linecap="round"/>
    <line x1="128" y1="270" x2="148" y2="210" stroke="#c8c8d8" stroke-width="7" stroke-linecap="round"/>
    <ellipse cx="90" cy="295" rx="50" ry="22" fill="#b0b0c0" opacity="0.5"/>
    <rect x="65" y="295" width="50" height="45" rx="6" fill="#a8a8b8" opacity="0.4"/>
    <!-- MCP joints with erosion markers -->
    <circle cx="70" cy="230" r="6" fill="none" stroke="#ff4444" stroke-width="1.5" stroke-dasharray="3,2"/>
    <circle cx="90" cy="222" r="6" fill="none" stroke="#ff4444" stroke-width="1.5" stroke-dasharray="3,2"/>
    <circle cx="110" cy="225" r="6" fill="none" stroke="#ff4444" stroke-width="1.5" stroke-dasharray="3,2"/>
    <text x="125" y="225" fill="#ff6666" font-size="8" font-family="sans-serif">erosions</text>
    <!-- Swelling indicator -->
    <ellipse cx="80" cy="228" rx="30" ry="15" fill="#ff4444" opacity="0.08"/>
  </g>
  <!-- Right hand -->
  <g opacity="0.85">
    <line x1="280" y1="280" x2="280" y2="180" stroke="#c8c8d8" stroke-width="10" stroke-linecap="round"/>
    <line x1="260" y1="278" x2="250" y2="175" stroke="#c8c8d8" stroke-width="8" stroke-linecap="round"/>
    <line x1="300" y1="280" x2="315" y2="180" stroke="#c8c8d8" stroke-width="8" stroke-linecap="round"/>
    <line x1="242" y1="275" x2="225" y2="195" stroke="#c8c8d8" stroke-width="7" stroke-linecap="round"/>
    <line x1="318" y1="270" x2="340" y2="210" stroke="#c8c8d8" stroke-width="7" stroke-linecap="round"/>
    <ellipse cx="280" cy="295" rx="50" ry="22" fill="#b0b0c0" opacity="0.5"/>
    <rect x="255" y="295" width="50" height="45" rx="6" fill="#a8a8b8" opacity="0.4"/>
    <circle cx="260" cy="228" r="5" fill="none" stroke="#ff4444" stroke-width="1.5" stroke-dasharray="3,2"/>
    <circle cx="280" cy="222" r="5" fill="none" stroke="#ff4444" stroke-width="1.5" stroke-dasharray="3,2"/>
  </g>
  <text x="80" y="345" fill="#666" font-size="9" font-family="monospace">L</text>
  <text x="285" y="345" fill="#666" font-size="9" font-family="monospace">R</text>
  <!-- Annotation -->
  <rect x="160" y="140" width="80" height="24" rx="4" fill="#ff4444" opacity="0.15"/>
  <text x="170" y="156" fill="#ff6666" font-size="9" font-weight="bold" font-family="sans-serif">RA CHANGES</text>
</svg>'''
    elif img_type == "X-Ray" and "Chest" in region:
        return '''<svg viewBox="0 0 400 420" xmlns="http://www.w3.org/2000/svg" style="width:100%;max-width:400px;border-radius:8px">
  <rect width="400" height="420" fill="#080810"/>
  <text x="10" y="18" fill="#4a6" font-size="10" font-family="monospace">X-RAY CHEST PA</text>
  <text x="300" y="18" fill="#888" font-size="9" font-family="monospace">MediSense</text>
  <!-- Ribcage -->
  <g opacity="0.6" stroke="#a0a8b8" stroke-width="2.5" fill="none">
    <path d="M120,100 Q200,85 280,100" /><path d="M115,125 Q200,108 285,125" />
    <path d="M112,150 Q200,132 288,150" /><path d="M110,175 Q200,155 290,175" />
    <path d="M112,200 Q200,180 288,200" /><path d="M115,225 Q200,205 285,225" />
    <path d="M120,250 Q200,230 280,250" /><path d="M125,275 Q200,258 275,275" />
  </g>
  <!-- Spine -->
  <line x1="200" y1="60" x2="200" y2="320" stroke="#b8b8c8" stroke-width="6" opacity="0.4"/>
  <!-- Heart silhouette -->
  <ellipse cx="215" cy="210" rx="55" ry="60" fill="#8888a0" opacity="0.25"/>
  <path d="M170,180 Q215,140 260,180 Q260,250 215,270 Q170,250 170,180Z" fill="#7a7a90" opacity="0.2"/>
  <!-- Lung fields -->
  <ellipse cx="145" cy="190" rx="55" ry="85" fill="#222235" opacity="0.4"/>
  <ellipse cx="260" cy="190" rx="50" ry="85" fill="#222235" opacity="0.4"/>
  <!-- Clavicles -->
  <path d="M130,80 Q200,65 270,80" stroke="#c0c0d0" stroke-width="4" fill="none" opacity="0.7"/>
  <!-- Shoulder joints -->
  <circle cx="105" cy="90" r="18" stroke="#b0b0c0" stroke-width="2" fill="none" opacity="0.5"/>
  <circle cx="295" cy="90" r="18" stroke="#b0b0c0" stroke-width="2" fill="none" opacity="0.5"/>
  <!-- Diaphragm -->
  <path d="M110,290 Q150,275 200,285 Q250,275 290,290" stroke="#a0a0b0" stroke-width="2" fill="none" opacity="0.5"/>
  <!-- L/R markers -->
  <text x="18" y="110" fill="#aaa" font-size="16" font-weight="bold" font-family="sans-serif">L</text>
  <text x="372" y="110" fill="#aaa" font-size="16" font-weight="bold" font-family="sans-serif">R</text>
  <!-- Label -->
  <rect x="130" y="380" width="140" height="24" rx="4" fill="#1a5" opacity="0.2"/>
  <text x="145" y="397" fill="#3c8" font-size="10" font-weight="bold" font-family="sans-serif">CLEAR LUNG FIELDS</text>
</svg>'''
    elif img_type == "ECG":
        return '''<svg viewBox="0 0 500 280" xmlns="http://www.w3.org/2000/svg" style="width:100%;max-width:500px;border-radius:8px">
  <rect width="500" height="280" fill="#fefef5"/>
  <!-- Grid lines -->
  <defs>
    <pattern id="ecgSmall" width="5" height="5" patternUnits="userSpaceOnUse"><path d="M5,0 L0,0 0,5" fill="none" stroke="#fcc" stroke-width="0.3"/></pattern>
    <pattern id="ecgLarge" width="25" height="25" patternUnits="userSpaceOnUse"><rect fill="url(#ecgSmall)" width="25" height="25"/><path d="M25,0 L0,0 0,25" fill="none" stroke="#f99" stroke-width="0.5"/></pattern>
  </defs>
  <rect width="500" height="280" fill="url(#ecgLarge)"/>
  <text x="10" y="18" fill="#c33" font-size="10" font-weight="bold" font-family="monospace">12-LEAD ECG</text>
  <text x="350" y="18" fill="#888" font-size="9" font-family="monospace">25mm/s 10mm/mV</text>
  <!-- Lead II rhythm strip -->
  <text x="8" y="75" fill="#333" font-size="9" font-weight="bold" font-family="sans-serif">II</text>
  <polyline points="20,70 35,70 40,70 42,68 44,72 46,70 55,70 58,70 60,50 62,90 64,30 66,85 68,55 70,70 85,70 90,65 95,70 110,70 125,70 127,68 129,72 131,70 140,70 143,70 145,50 147,90 149,30 151,85 153,55 155,70 170,70 175,65 180,70 195,70 210,70 212,68 214,72 216,70 225,70 228,70 230,50 232,90 234,30 236,85 238,55 240,70 255,70 260,65 265,70 280,70 295,70 297,68 299,72 301,70 310,70 313,70 315,50 317,90 319,30 321,85 323,55 325,70 340,70 345,65 350,70 365,70 380,70 382,68 384,72 386,70 395,70 398,70 400,50 402,90 404,30 406,85 408,55 410,70 425,70 430,65 435,70 450,70 480,70" fill="none" stroke="#111" stroke-width="1.2"/>
  <!-- Lead V4 with ST depression -->
  <text x="8" y="145" fill="#333" font-size="9" font-weight="bold" font-family="sans-serif">V4</text>
  <polyline points="20,140 40,140 42,138 44,142 46,140 55,140 58,140 60,125 62,155 64,105 66,160 68,130 70,145 85,145 90,143 95,145 110,145 125,145 127,143 129,147 131,145 140,145 143,145 145,130 147,160 149,110 151,165 153,135 155,150 170,150 175,148 180,150 195,145 210,145 212,143 214,147 216,145 225,145 228,145 230,130 232,160 234,110 236,165 238,135 240,150 255,150 260,148 265,150 280,145 295,145 297,143 299,147 301,145 310,145 313,145 315,130 317,160 319,110 321,165 323,135 325,150 340,150 345,148 350,150 365,145 380,145 382,143 384,147 386,145 395,145 398,145 400,130 402,160 404,110 406,165 408,135 410,150 425,150 430,148 435,150 480,145" fill="none" stroke="#111" stroke-width="1.2"/>
  <!-- ST depression annotation -->
  <line x1="70" y1="148" x2="85" y2="148" stroke="#e33" stroke-width="2"/>
  <text x="68" y="165" fill="#c33" font-size="8" font-weight="bold" font-family="sans-serif">ST&#x2193; 1mm</text>
  <!-- Lead V5 -->
  <text x="8" y="215" fill="#333" font-size="9" font-weight="bold" font-family="sans-serif">V5</text>
  <polyline points="20,210 40,210 42,208 44,212 46,210 58,210 60,195 62,225 64,180 66,230 68,200 70,215 85,215 90,213 95,215 110,215 125,215 127,213 129,217 131,215 143,215 145,200 147,230 149,185 151,233 153,203 155,218 170,218 175,216 180,218 195,215 210,215 212,213 214,217 216,215 228,215 230,200 232,230 234,185 236,233 238,203 240,218 255,218 260,216 265,218 280,215 295,215 297,213 299,217 301,215 313,215 315,200 317,230 319,185 321,233 323,203 325,218 340,218 345,216 350,218 365,215 398,215 400,200 402,230 404,185 406,233 408,203 410,218 425,218 430,216 435,218 480,215" fill="none" stroke="#111" stroke-width="1.2"/>
  <line x1="70" y1="218" x2="85" y2="218" stroke="#e33" stroke-width="2"/>
  <text x="68" y="233" fill="#c33" font-size="8" font-weight="bold" font-family="sans-serif">ST&#x2193; 1mm</text>
  <rect x="140" y="252" width="220" height="20" rx="4" fill="#c33" opacity="0.1"/>
  <text x="155" y="266" fill="#c33" font-size="10" font-weight="bold" font-family="sans-serif">ST DEPRESSION V4-V6 (1mm)</text>
</svg>'''
    elif img_type == "CT Scan" and "Abdomen" in region:
        return '''<svg viewBox="0 0 400 400" xmlns="http://www.w3.org/2000/svg" style="width:100%;max-width:400px;border-radius:8px">
  <rect width="400" height="400" fill="#000"/>
  <text x="10" y="18" fill="#4a6" font-size="10" font-family="monospace">CT ABDOMEN/PELVIS WITH CONTRAST</text>
  <text x="300" y="18" fill="#888" font-size="9" font-family="monospace">AXIAL</text>
  <!-- Body outline -->
  <ellipse cx="200" cy="210" rx="150" ry="120" fill="#1a1a22" stroke="#333" stroke-width="1"/>
  <!-- Subcutaneous fat ring -->
  <ellipse cx="200" cy="210" rx="140" ry="110" fill="none" stroke="#2a2a32" stroke-width="8" opacity="0.5"/>
  <!-- Spine -->
  <circle cx="200" cy="300" r="14" fill="#888" opacity="0.5"/>
  <circle cx="200" cy="300" r="8" fill="#555" opacity="0.5"/>
  <!-- Kidneys -->
  <ellipse cx="130" cy="240" rx="18" ry="28" fill="#4a4a55" opacity="0.6" transform="rotate(-15,130,240)"/>
  <ellipse cx="270" cy="240" rx="18" ry="28" fill="#4a4a55" opacity="0.6" transform="rotate(15,270,240)"/>
  <!-- Liver -->
  <path d="M230,150 Q310,160 300,220 Q280,250 240,240 Q210,230 210,190 Q210,155 230,150Z" fill="#3a3a45" opacity="0.5"/>
  <text x="250" y="195" fill="#666" font-size="8" font-family="sans-serif">Liver</text>
  <!-- Spleen -->
  <ellipse cx="115" cy="180" rx="25" ry="30" fill="#3a3a45" opacity="0.5"/>
  <text x="98" y="185" fill="#666" font-size="8" font-family="sans-serif">Spleen</text>
  <!-- Aorta -->
  <circle cx="195" cy="260" r="10" fill="#555" stroke="#666" stroke-width="1"/>
  <!-- Appendix area with inflammation marker -->
  <circle cx="290" cy="270" r="18" fill="#ff3333" opacity="0.15"/>
  <circle cx="290" cy="270" r="12" fill="#ff3333" opacity="0.1"/>
  <circle cx="290" cy="270" r="6" fill="#ff4444" opacity="0.25"/>
  <text x="260" y="300" fill="#ff6666" font-size="9" font-weight="bold" font-family="sans-serif">RIF mass</text>
  <!-- Arrow pointing to finding -->
  <line x1="310" y1="255" x2="298" y2="265" stroke="#ff6666" stroke-width="1.5"/>
  <polygon points="298,265 302,258 305,264" fill="#ff6666"/>
  <!-- Bowel loops -->
  <circle cx="200" cy="220" r="8" fill="none" stroke="#3a3a45" stroke-width="2"/>
  <circle cx="220" cy="230" r="7" fill="none" stroke="#3a3a45" stroke-width="2"/>
  <circle cx="180" cy="230" r="7" fill="none" stroke="#3a3a45" stroke-width="2"/>
  <!-- Window info -->
  <text x="10" y="390" fill="#555" font-size="8" font-family="monospace">W:350 L:50 SOFT TISSUE</text>
  <text x="280" y="390" fill="#555" font-size="8" font-family="monospace">Slice 42/86</text>
</svg>'''
    elif img_type == "CT Scan" and "Head" in region:
        return '''<svg viewBox="0 0 400 400" xmlns="http://www.w3.org/2000/svg" style="width:100%;max-width:400px;border-radius:8px">
  <rect width="400" height="400" fill="#000"/>
  <text x="10" y="18" fill="#4a6" font-size="10" font-family="monospace">CT HEAD NON-CONTRAST</text>
  <text x="310" y="18" fill="#888" font-size="9" font-family="monospace">AXIAL</text>
  <!-- Skull -->
  <ellipse cx="200" cy="200" rx="130" ry="150" fill="#1a1a22" stroke="#888" stroke-width="4"/>
  <ellipse cx="200" cy="200" rx="120" ry="140" fill="#1a1a22"/>
  <!-- Brain matter -->
  <ellipse cx="200" cy="195" rx="110" ry="125" fill="#2a2a35"/>
  <!-- Midline -->
  <line x1="200" y1="70" x2="200" y2="320" stroke="#444" stroke-width="0.8" stroke-dasharray="4,3"/>
  <!-- Ventricles -->
  <path d="M175,170 Q185,155 200,160 Q215,155 225,170 L220,175 Q200,168 180,175Z" fill="#111" opacity="0.7"/>
  <!-- Sulci pattern -->
  <g stroke="#222" stroke-width="0.8" fill="none" opacity="0.6">
    <path d="M130,150 Q160,140 190,150"/><path d="M210,150 Q240,140 270,150"/>
    <path d="M120,180 Q150,170 180,180"/><path d="M220,180 Q250,170 280,180"/>
    <path d="M115,210 Q145,200 175,210"/><path d="M225,210 Q255,200 285,210"/>
    <path d="M120,240 Q150,230 180,240"/><path d="M220,240 Q250,230 280,240"/>
  </g>
  <!-- L/R markers -->
  <text x="40" y="205" fill="#aaa" font-size="14" font-weight="bold" font-family="sans-serif">L</text>
  <text x="345" y="205" fill="#aaa" font-size="14" font-weight="bold" font-family="sans-serif">R</text>
  <rect x="120" y="370" width="160" height="20" rx="4" fill="#1a5" opacity="0.2"/>
  <text x="130" y="384" fill="#3c8" font-size="10" font-weight="bold" font-family="sans-serif">NO ACUTE PATHOLOGY</text>
  <text x="10" y="390" fill="#555" font-size="8" font-family="monospace">W:80 L:40 BRAIN</text>
</svg>'''
    elif img_type == "Ultrasound":
        return '''<svg viewBox="0 0 400 350" xmlns="http://www.w3.org/2000/svg" style="width:100%;max-width:400px;border-radius:8px">
  <rect width="400" height="350" fill="#000"/>
  <text x="10" y="18" fill="#4a6" font-size="10" font-family="monospace">ULTRASOUND</text>
  <text x="300" y="18" fill="#888" font-size="9" font-family="monospace">MediSense</text>
  <!-- Ultrasound cone -->
  <defs>
    <radialGradient id="usGrad" cx="50%" cy="10%" r="70%">
      <stop offset="0%" stop-color="#333340"/><stop offset="60%" stop-color="#1a1a25"/>
      <stop offset="100%" stop-color="#0a0a12"/>
    </radialGradient>
  </defs>
  <path d="M160,35 L40,330 L360,330 L240,35Z" fill="url(#usGrad)"/>
  <!-- Speckle noise texture -->
  <g opacity="0.3">
    <circle cx="150" cy="120" r="1.5" fill="#555"/><circle cx="180" cy="130" r="1" fill="#666"/>
    <circle cx="210" cy="115" r="1.2" fill="#555"/><circle cx="240" cy="140" r="1" fill="#444"/>
    <circle cx="130" cy="160" r="1.3" fill="#555"/><circle cx="170" cy="170" r="1" fill="#666"/>
    <circle cx="200" cy="155" r="1.5" fill="#555"/><circle cx="230" cy="165" r="1.2" fill="#444"/>
    <circle cx="260" cy="150" r="1" fill="#555"/><circle cx="140" cy="200" r="1.4" fill="#666"/>
    <circle cx="180" cy="210" r="1" fill="#555"/><circle cx="220" cy="195" r="1.3" fill="#444"/>
    <circle cx="250" cy="210" r="1" fill="#555"/><circle cx="160" cy="240" r="1.2" fill="#666"/>
    <circle cx="200" cy="250" r="1.5" fill="#555"/><circle cx="240" cy="240" r="1" fill="#444"/>
    <circle cx="120" cy="270" r="1" fill="#555"/><circle cx="280" cy="260" r="1.3" fill="#666"/>
    <circle cx="200" cy="280" r="1" fill="#555"/><circle cx="160" cy="290" r="1.2" fill="#444"/>
  </g>
  <!-- Organ structure -->
  <ellipse cx="200" cy="180" rx="60" ry="45" fill="none" stroke="#556" stroke-width="1.5" opacity="0.6"/>
  <ellipse cx="200" cy="180" rx="50" ry="35" fill="#1e1e2a" opacity="0.3"/>
  <!-- Depth markers -->
  <g stroke="#333" stroke-width="0.5">
    <line x1="365" y1="80" x2="375" y2="80"/><text x="377" y="84" fill="#444" font-size="7" font-family="monospace">2cm</text>
    <line x1="365" y1="155" x2="375" y2="155"/><text x="377" y="159" fill="#444" font-size="7" font-family="monospace">5cm</text>
    <line x1="365" y1="230" x2="375" y2="230"/><text x="377" y="234" fill="#444" font-size="7" font-family="monospace">8cm</text>
    <line x1="365" y1="305" x2="375" y2="305"/><text x="377" y="309" fill="#444" font-size="7" font-family="monospace">11cm</text>
  </g>
  <!-- Measurement calipers -->
  <line x1="155" y1="180" x2="245" y2="180" stroke="#0ff" stroke-width="0.8" stroke-dasharray="3,2"/>
  <line x1="155" y1="175" x2="155" y2="185" stroke="#0ff" stroke-width="1"/>
  <line x1="245" y1="175" x2="245" y2="185" stroke="#0ff" stroke-width="1"/>
  <text x="185" y="175" fill="#0ff" font-size="9" font-family="monospace">3.2 cm</text>
</svg>'''
    elif img_type == "Stress Test":
        return '''<svg viewBox="0 0 480 260" xmlns="http://www.w3.org/2000/svg" style="width:100%;max-width:480px;border-radius:8px">
  <rect width="480" height="260" fill="#fafafa" stroke="#ddd" stroke-width="1"/>
  <text x="15" y="20" fill="#333" font-size="11" font-weight="bold" font-family="sans-serif">EXERCISE STRESS TEST</text>
  <text x="350" y="20" fill="#888" font-size="9" font-family="monospace">Bruce Protocol</text>
  <!-- Grid -->
  <g stroke="#eee" stroke-width="0.5">
    <line x1="50" y1="40" x2="50" y2="220"/><line x1="50" y1="220" x2="460" y2="220"/>
    <line x1="50" y1="175" x2="460" y2="175" stroke-dasharray="3,3"/>
    <line x1="50" y1="130" x2="460" y2="130" stroke-dasharray="3,3"/>
    <line x1="50" y1="85" x2="460" y2="85" stroke-dasharray="3,3"/>
  </g>
  <!-- Y axis labels -->
  <text x="10" y="223" fill="#666" font-size="8" font-family="sans-serif">60</text>
  <text x="10" y="178" fill="#666" font-size="8" font-family="sans-serif">100</text>
  <text x="10" y="133" fill="#666" font-size="8" font-family="sans-serif">140</text>
  <text x="10" y="88" fill="#666" font-size="8" font-family="sans-serif">180</text>
  <text x="5" y="50" fill="#666" font-size="8" font-family="sans-serif">HR bpm</text>
  <!-- X axis labels -->
  <text x="50" y="237" fill="#666" font-size="8" font-family="sans-serif">0</text>
  <text x="140" y="237" fill="#666" font-size="8" font-family="sans-serif">3 min</text>
  <text x="230" y="237" fill="#666" font-size="8" font-family="sans-serif">6 min</text>
  <text x="320" y="237" fill="#666" font-size="8" font-family="sans-serif">9 min</text>
  <text x="400" y="237" fill="#666" font-size="8" font-family="sans-serif">Recovery</text>
  <!-- HR curve -->
  <polyline points="50,210 80,205 110,195 140,180 170,165 200,145 230,120 260,100 290,85 310,80 330,78 340,82 370,110 400,140 430,165 460,180" fill="none" stroke="#e33" stroke-width="2.5"/>
  <!-- Target HR line -->
  <line x1="50" y1="88" x2="340" y2="88" stroke="#fa0" stroke-width="1" stroke-dasharray="6,3"/>
  <text x="290" y="83" fill="#fa0" font-size="8" font-family="sans-serif">Target HR 85%</text>
  <!-- Achieved mark -->
  <circle cx="330" cy="78" r="5" fill="#e33" opacity="0.3"/>
  <circle cx="330" cy="78" r="2.5" fill="#e33"/>
  <text x="337" y="75" fill="#e33" font-size="8" font-weight="bold" font-family="sans-serif">Peak 178 bpm</text>
  <!-- ST depression note -->
  <rect x="200" y="245" width="180" height="14" rx="3" fill="#fe0" opacity="0.2"/>
  <text x="210" y="256" fill="#b80" font-size="9" font-weight="bold" font-family="sans-serif">ST depression at peak exercise</text>
</svg>'''
    elif img_type == "Spirometry":
        return '''<svg viewBox="0 0 480 280" xmlns="http://www.w3.org/2000/svg" style="width:100%;max-width:480px;border-radius:8px">
  <rect width="480" height="280" fill="#fafafa" stroke="#ddd" stroke-width="1"/>
  <text x="15" y="22" fill="#333" font-size="11" font-weight="bold" font-family="sans-serif">SPIROMETRY - FLOW-VOLUME LOOP</text>
  <text x="360" y="22" fill="#888" font-size="9" font-family="monospace">MediSense PFT</text>
  <!-- Axes -->
  <line x1="60" y1="145" x2="440" y2="145" stroke="#999" stroke-width="1"/>
  <line x1="60" y1="40" x2="60" y2="250" stroke="#999" stroke-width="1"/>
  <!-- Grid -->
  <g stroke="#eee" stroke-width="0.5">
    <line x1="60" y1="70" x2="440" y2="70"/><line x1="60" y1="100" x2="440" y2="100"/>
    <line x1="60" y1="175" x2="440" y2="175"/><line x1="60" y1="210" x2="440" y2="210"/>
    <line x1="155" y1="40" x2="155" y2="250"/><line x1="250" y1="40" x2="250" y2="250"/>
    <line x1="345" y1="40" x2="345" y2="250"/>
  </g>
  <!-- Y axis: Flow (L/s) -->
  <text x="15" y="75" fill="#666" font-size="8" font-family="sans-serif">8</text>
  <text x="15" y="105" fill="#666" font-size="8" font-family="sans-serif">6</text>
  <text x="15" y="148" fill="#666" font-size="8" font-family="sans-serif">0</text>
  <text x="15" y="180" fill="#666" font-size="8" font-family="sans-serif">-2</text>
  <text x="15" y="215" fill="#666" font-size="8" font-family="sans-serif">-4</text>
  <text x="5" y="42" fill="#666" font-size="8" font-family="sans-serif">Flow L/s</text>
  <!-- X axis Volume (L) -->
  <text x="150" y="262" fill="#666" font-size="8" font-family="sans-serif">1L</text>
  <text x="245" y="262" fill="#666" font-size="8" font-family="sans-serif">2L</text>
  <text x="340" y="262" fill="#666" font-size="8" font-family="sans-serif">3L</text>
  <text x="420" y="262" fill="#666" font-size="8" font-family="sans-serif">Vol(L)</text>
  <!-- Normal predicted curve (dashed) -->
  <path d="M60,145 Q80,50 120,55 Q200,60 300,100 Q380,130 410,145" fill="none" stroke="#aaa" stroke-width="1.5" stroke-dasharray="5,3"/>
  <path d="M60,145 Q100,190 200,205 Q350,215 410,145" fill="none" stroke="#aaa" stroke-width="1.5" stroke-dasharray="5,3"/>
  <text x="350" y="95" fill="#aaa" font-size="8" font-family="sans-serif">Predicted</text>
  <!-- Obstructive pattern curve (actual - concave) -->
  <path d="M60,145 Q75,70 105,78 Q160,95 220,115 Q300,135 340,145" fill="none" stroke="#e33" stroke-width="2.5"/>
  <path d="M60,145 Q90,175 180,190 Q280,200 340,145" fill="none" stroke="#2563eb" stroke-width="2.5"/>
  <text x="200" y="190" fill="#2563eb" font-size="8" font-family="sans-serif">Actual</text>
  <!-- Concavity annotation -->
  <path d="M140,100 Q160,110 180,108" fill="none" stroke="#e33" stroke-width="1" marker-end="url(#arrowRed)"/>
  <text x="130" y="95" fill="#e33" font-size="8" font-weight="bold" font-family="sans-serif">Concave</text>
  <!-- Results box -->
  <rect x="330" y="40" width="110" height="58" rx="4" fill="#fff" stroke="#ddd"/>
  <text x="340" y="55" fill="#333" font-size="8" font-weight="bold" font-family="sans-serif">FEV1: 1.8L (55%)</text>
  <text x="340" y="68" fill="#333" font-size="8" font-weight="bold" font-family="sans-serif">FVC: 3.2L (78%)</text>
  <text x="340" y="81" fill="#e33" font-size="8" font-weight="bold" font-family="sans-serif">FEV1/FVC: 0.56</text>
  <text x="340" y="93" fill="#e33" font-size="8" font-weight="bold" font-family="sans-serif">OBSTRUCTIVE</text>
</svg>'''
    else:
        # Generic placeholder for any other type
        icon = {"X-Ray": "&#x2622;", "CT Scan": "&#x25CE;", "MRI": "&#x25CE;"}.get(img_type, "&#x25A3;")
        return f'''<svg viewBox="0 0 400 200" xmlns="http://www.w3.org/2000/svg" style="width:100%;max-width:400px;border-radius:8px">
  <rect width="400" height="200" fill="#0a0a14"/>
  <text x="10" y="18" fill="#4a6" font-size="10" font-family="monospace">{img_type.upper()}</text>
  <rect x="50" y="40" width="300" height="120" rx="8" fill="#14141e" stroke="#333" stroke-width="1"/>
  <text x="200" y="105" fill="#555" font-size="14" text-anchor="middle" font-family="sans-serif">{img_type} Image - {region}</text>
  <text x="200" y="125" fill="#444" font-size="10" text-anchor="middle" font-family="monospace">Digital DICOM Viewer</text>
</svg>'''


@app.route("/patient-files/<patient_id>")
def patient_files(patient_id):
    """Render a standalone clinical files page (lab reports + imaging) for screen sharing."""
    from markupsafe import escape as html_escape

    p = next((x for x in SAMPLE_PATIENTS if x["id"] == patient_id), None)
    if not p:
        return "Patient not found", 404

    # Build lab results HTML
    labs = p.get("labs", {})
    esr_list = labs.get("esr", [])
    other_labs = {k: v for k, v in labs.items() if k != "esr"}

    lab_rows = ""
    for e in esr_list:
        flag = ' <span style="color:#dc2626;font-weight:700">HIGH</span>' if e["value"] > 20 else ""
        lab_rows += f'<tr><td>ESR</td><td style="font-weight:700">{e["value"]} {e["unit"]}{flag}</td><td>{e["ref"]}</td><td>{e["date"]}</td></tr>'
    for k, v in other_labs.items():
        v_str = str(v)
        flag_class = ""
        if "HIGH" in v_str.upper():
            flag_class = "color:#dc2626;font-weight:700"
        elif "LOW" in v_str.upper():
            flag_class = "color:#d97706;font-weight:700"
        elif "POSITIVE" in v_str.upper() or "BORDERLINE" in v_str.upper():
            flag_class = "color:#dc2626;font-weight:700"
        style = f' style="{flag_class}"' if flag_class else ""
        lab_rows += f'<tr><td>{html_escape(k.upper().replace("_", " "))}</td><td{style}>{html_escape(v_str)}</td><td>—</td><td>Latest</td></tr>'

    # Build imaging/radiology HTML
    imaging = p.get("imaging", [])
    imaging_html = ""
    for img in imaging:
        badge_color = {"X-Ray": "#2563eb", "CT Scan": "#7c3aed", "ECG": "#dc2626",
                       "Ultrasound": "#0891b2", "Stress Test": "#b45309",
                       "Spirometry": "#059669", "MRI": "#6d28d9"}.get(img["type"], "#475569")
        svg_image = _generate_imaging_svg(img["type"], img["region"], img.get("report", ""))
        imaging_html += f'''
        <div style="background:#fff;border:1px solid #e2e8f0;border-radius:12px;padding:20px;margin-bottom:16px;page-break-inside:avoid">
            <div style="display:flex;align-items:center;gap:10px;margin-bottom:12px">
                <span style="background:{badge_color};color:#fff;padding:3px 10px;border-radius:6px;font-size:11px;font-weight:700;letter-spacing:0.05em">{html_escape(img["type"])}</span>
                <span style="font-weight:700;color:#1e293b;font-size:14px">{html_escape(img["region"])}</span>
                <span style="margin-left:auto;color:#94a3b8;font-size:12px">{html_escape(img["date"])}</span>
            </div>
            <div style="text-align:center;background:#000;border-radius:8px;padding:12px;margin-bottom:12px">
                {svg_image}
            </div>
            <div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;padding:14px;margin-bottom:10px">
                <div style="font-size:10px;font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:0.08em;margin-bottom:6px">REPORT</div>
                <p style="font-size:13px;line-height:1.6;color:#334155;margin:0">{html_escape(img["report"])}</p>
            </div>
            <div style="background:#fef2f2;border:1px solid #fecaca;border-radius:8px;padding:12px">
                <div style="font-size:10px;font-weight:700;color:#991b1b;text-transform:uppercase;letter-spacing:0.08em;margin-bottom:4px">IMPRESSION</div>
                <p style="font-size:13px;line-height:1.5;color:#991b1b;font-weight:600;margin:0">{html_escape(img["impression"])}</p>
            </div>
        </div>'''

    # Medications HTML
    med_rows = ""
    for m in p.get("medications", []):
        med_rows += f'<tr><td style="font-weight:600">{html_escape(m["name"])}</td><td>{html_escape(m["dose"])}</td><td>{html_escape(m["frequency"])}</td></tr>'

    # Allergies
    allergy_str = ", ".join(p.get("allergies", ["None known"]))
    allergy_badge = f'<span style="background:#fef2f2;color:#dc2626;border:1px solid #fecaca;padding:4px 12px;border-radius:8px;font-weight:700;font-size:12px">\u26a0\ufe0f ALLERGIES: {html_escape(allergy_str)}</span>' if p.get("allergies") else ""

    # Vitals HTML
    vitals = p.get("vitals", {})
    vital_badges = ""
    for k, v in vitals.items():
        label = k.upper().replace("_", " ")
        vital_badges += f'<span style="background:#f0fdf4;border:1px solid #bbf7d0;padding:4px 10px;border-radius:8px;font-size:12px"><strong>{html_escape(label)}</strong> {html_escape(str(v))}</span> '

    html = f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Clinical Files \u2014 {html_escape(p["name"])}</title>
    <style>
        * {{ margin:0; padding:0; box-sizing:border-box; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif; background:#f1f5f9; color:#1e293b; }}
        .header {{ background:linear-gradient(135deg,#0f172a,#1e3a5f); color:#fff; padding:24px 32px; }}
        .header h1 {{ font-size:22px; font-weight:800; }}
        .header .sub {{ font-size:13px; opacity:0.7; margin-top:4px; }}
        .header .info {{ display:flex; gap:12px; flex-wrap:wrap; margin-top:12px; align-items:center; }}
        .header .info span {{ background:rgba(255,255,255,0.15); padding:3px 10px; border-radius:6px; font-size:12px; }}
        .container {{ max-width:1000px; margin:0 auto; padding:24px; }}
        h2 {{ font-size:16px; font-weight:800; color:#0f172a; margin:24px 0 12px; display:flex; align-items:center; gap:8px; }}
        h2 .icon {{ width:28px; height:28px; border-radius:8px; display:flex; align-items:center; justify-content:center; font-size:14px; }}
        table {{ width:100%; border-collapse:collapse; background:#fff; border-radius:10px; overflow:hidden; border:1px solid #e2e8f0; }}
        th {{ background:#f8fafc; text-align:left; padding:10px 14px; font-size:11px; font-weight:700; color:#475569; text-transform:uppercase; letter-spacing:0.05em; border-bottom:2px solid #e2e8f0; }}
        td {{ padding:10px 14px; font-size:13px; border-bottom:1px solid #f1f5f9; }}
        tr:last-child td {{ border-bottom:none; }}
        .print-btn {{ position:fixed; top:16px; right:16px; background:#0891b2; color:#fff; border:none; padding:10px 20px; border-radius:10px; font-weight:700; font-size:13px; cursor:pointer; z-index:100; }}
        .print-btn:hover {{ background:#0e7490; }}
        @media print {{ .print-btn {{ display:none; }} body {{ background:#fff; }} }}
    </style>
</head>
<body>
    <button class="print-btn" onclick="window.print()">🖨 Print</button>
    <div class="header">
        <h1>&#127973; {html_escape(p["name"])} \u2014 Clinical Files</h1>
        <div class="sub">MediSense Patient Record \u00b7 Generated {datetime.now().strftime("%Y-%m-%d %H:%M")}</div>
        <div class="info">
            <span>ID: {html_escape(p["id"])}</span>
            <span>Age: {p["age"]}</span>
            <span>{html_escape(p["gender"])}</span>
            <span>DOB: {html_escape(p["dob"])}</span>
            <span>Blood: {html_escape(p["blood_type"])}</span>
            {allergy_badge}
        </div>
    </div>
    <div class="container">
        <div style="display:flex;flex-wrap:wrap;gap:6px;margin-bottom:20px">{vital_badges}</div>

        <h2><span class="icon" style="background:#dbeafe;border:1px solid #93c5fd">&#128138;</span> Current Medications</h2>
        <table>
            <thead><tr><th>Medication</th><th>Dose</th><th>Frequency</th></tr></thead>
            <tbody>{med_rows}</tbody>
        </table>

        <h2><span class="icon" style="background:#fef3c7;border:1px solid #fcd34d">&#129514;</span> Laboratory Results</h2>
        <table>
            <thead><tr><th>Test</th><th>Result</th><th>Reference</th><th>Date</th></tr></thead>
            <tbody>{lab_rows}</tbody>
        </table>

        <h2><span class="icon" style="background:#ede9fe;border:1px solid #c4b5fd">&#128247;</span> Imaging &amp; Radiology Reports</h2>
        {imaging_html if imaging_html else '<p style="color:#94a3b8;font-style:italic;padding:16px">No imaging studies on file.</p>'}

        <p style="text-align:center;color:#94a3b8;font-size:11px;margin-top:32px;padding-bottom:16px">\u26a0\ufe0f AI assistant only \u00b7 Not a substitute for professional medical advice \u00b7 Emergency: call 911</p>
    </div>
</body>
</html>'''
    return html


@app.route("/")
def serve_home():
    return send_from_directory(".", "index.html")


@app.route("/src/<path:filename>")
def serve_src_files(filename):
    return send_from_directory("src", filename)


@app.route("/style.css")
def serve_style():
    if Path("style.css").exists():
        return send_from_directory(".", "style.css")
    return "", 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    print("=" * 60)
    print("  MediSense - Remote Emergency Healthcare Co-Pilot")
    print(f"  Project: {DEFAULT_PROJECT_ID or 'Not configured'}")
    print(f"  Running at: http://localhost:{port}")
    print("=" * 60)
    socketio.run(app, host="0.0.0.0", port=port, debug=False)
