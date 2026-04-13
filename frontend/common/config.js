/**
 * B.U Eats — API Configuration
 *
 * HOW IT WORKS:
 *  - In local development  → points to http://127.0.0.1:5000
 *  - After Railway deploy  → replace RAILWAY_URL below with your Railway URL
 *    e.g. "https://bu-eats-production.up.railway.app"
 *
 * You only need to change ONE line here and everything else updates automatically.
 */

const RAILWAY_URL = "https://smartcanteensystem-1.onrender.com";  // ✅ Render backend

// Auto-select: use Railway URL in production, localhost in dev
const API = RAILWAY_URL
  ? RAILWAY_URL
  : "http://127.0.0.1:5000";

// Freeze to prevent accidental mutation anywhere
Object.freeze({ API });
