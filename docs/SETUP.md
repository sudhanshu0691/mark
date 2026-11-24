# MARK (Windows desktop assistant) — Setup guide

## 1. Python version
**Python 3.11.x ya 3.12.x (64-bit)** install karo → https://www.python.org/downloads/windows/ (3.11.9 best hai).
Installer me **"Add python.exe to PATH"** tick karna. 3.10 se purana ya 3.14 mat lena (code `asyncio.TaskGroup` use karta hai = 3.11+, aur kuch libraries 3.13+ pe abhi stable nahi).

## 2. Install (ek baar)
1. Project folder ko kisi local drive par extract karo.
2. `setup_windows.bat` chalao; ye virtual environment, Python packages, aur Playwright setup karta hai.
3. Ollama install karke `.venv\Scripts\python.exe -m core.llm pull` chalao. Gemini Live voice ke liye Gemini API key chahiye.
4. `run_mark.bat` chalao. Keys ko dashboard ke **API KEYS** control se OS credential store me save karo.

Dependencies normal startup par install nahi hoti. Setup ke liye `setup_windows.bat` ya `python -m tooling.setup` chalao. Tesseract aur FFmpeg jaise external Windows tools alag se install karne pad sakte hain.

Full power (protected files, sab apps install): `run_as_admin.bat`. Login pe auto-start: `autostart_on_login.bat`.
## 3. Windows settings jo ek baar karni hain
| Kya | Kahan | Kyun |
|---|---|---|
| Microphone allow | Settings → Privacy → Microphone → Let desktop apps access = On | Voice ke liye |
| Notifications ON | Settings → System → Notifications → On, aur WhatsApp/Phone Link ke liye On | Notification padhne ke liye |
| Do-Not-Disturb / Focus OFF | Same page | DND me popup nahi aata → call button nahi milta |
| WhatsApp **Desktop** (Microsoft Store) install + login | Store → WhatsApp | Message/call isi se hote hain. Settings → Notifications me "Show message preview" ON |
| winget | Store → "App Installer" (Win 10/11 me aksar pehle se hota hai) | Software install ke liye |
| Phone Link (optional) | Store → Phone Link, apne Android se pair | Normal SIM calls uthane/kaatne ke liye |
| Folder permission | Agar "access denied" aaye → `run_as_admin.bat` | Protected folders |

## 4. File access kaise control hota hai
`config/permissions.json` banane ke liye starter config copy karein:
```bat
copy config\permissions.example.json config\permissions.json
```

Iske baad `config/permissions.json` me access policy edit karein:
```json
{
  "allowed_roots": [],            // [] = saari drives (C:, D:, USB...). Limit karni ho: ["C:\\Users\\Me", "D:\\"]
  "allow_system_paths": false,    // true = C:\Windows / Program Files me bhi write/delete
  "delete_mode": "recycle",       // "recycle" = Recycle Bin (wapas aa sakta hai) | "permanent"
  "confirm_permanent_delete": true,
  "extra_blocked_paths": []       // jin folders ko kabhi nahi chhuna
}
```
MARK read / write / append / copy / move / rename / delete / find (naam, extension, andar ka text) / zip / unzip / open sab karta hai, aur har change ke baad **check karta hai** (file exist karti hai? checksum match? source gayab?).

## 5. WhatsApp contacts
`config/contacts.json` me naam → number (country code ke saath, sirf digits). Starter file ke liye:
```bat
copy config\contacts.example.json config\contacts.json
```

Uske baad `config/contacts.json` edit karein:
```json
{ "contacts": { "rahul": "919876543210", "mom": "919812345678" } }
```
Number hone par official `whatsapp://` link use hota hai (100% sahi insaan). Number na ho to naam se search karta hai, **lekin chat header confirm na ho to message bhejta hi nahi** (galat insaan ko na chala jaye).

## 6. Kuch bhi bolo (examples)
- "Rahul ko WhatsApp pe bolo main 10 min me aa raha hoon"
- "Naye notifications padho" / "notification alerts band karo"
- "Call utha lo" / "call kaat do" / "Mom ko WhatsApp video call lagao"
- "D drive me resume.pdf dhundo aur Desktop pe copy karo"
- "Downloads se purani .zip files Recycle Bin me daalo"
- "VLC install karo" / "Chrome uninstall karo"
- "VS Code install karo, phir usme project folder kholo" → `run_goal` (brain khud plan banata hai)
- "Ek plugin banao jo ..." → Mark `plugins/` me khud file likh deta hai

## 7. Plugin folder
`plugins/` me `.py` daalo → ~2 sec me auto-load (Mark thoda reconnect hota hai taaki naya tool dikhe). Template: `plugins/_template.py`. Example: `plugins/clipboard_tools.py`. Plugin ko har Windows notification bhi mil sakti hai (`on_notification` hook).

Screen vision supports monitor selection, pixel regions, and optional local OCR. Install the Python `pytesseract` package and the Windows Tesseract OCR engine to enable OCR; without the engine, MARK reports OCR as unavailable and continues with image vision.

Plugins can declare `METADATA` with `version`, `capabilities`, `permissions`, and `timeout`, plus an optional `health_check(ctx)`. Ask MARK to discover plugins for capability-aware selection.

## 8. "Done" kab bolta hai — honesty system
Har action 3 me se ek status deta hai:
- **DONE** – effect check ho gaya (file exist karti hai, bubble chat me dikha, winget list me app aaya)
- **UNVERIFIED** – action chala par confirm nahi ho paya → Mark "try kiya, confirm nahi hua" bolta hai + wajah
- **FAILED** – nahi hua → Mark wajah batata hai (e.g. "WhatsApp Desktop installed nahi hai")

## 9. Honest limits (jo guarantee nahi ho sakta)
- Ye code Linux pe compile + logic-test hua hai; **Windows pe live (WhatsApp, calls, winget, notifications) maine run nahi kiya**. Pehli baar chalane par kuch UI-Automation names tumhare WhatsApp version se alag ho sakte hain — tab Mark FAILED/UNVERIFIED me exact wajah dikhayega, bata dena, theek kar denge.
- Call uthana tab hi chalta hai jab Windows popup/WhatsApp window me Accept/Decline button dikhe. Normal SIM call ke liye Phone Link zaroori hai.
- Notification text wahi milta hai jo Windows popup me dikhta hai (preview off → text nahi milega).
- Software install kabhi UAC (Yes/No box) maangta hai — wo tumhe khud dabana padta hai.
- Phone remote/QR/dashboard wala hissa **hata diya gaya hai**; ab sirf is PC ka control hai.

## 10. Security
- If an API key was ever committed or shared, revoke it in Google AI Studio and
  create a replacement.
- Is software ko poora PC-access hai; `permissions.json` se limit laga sakte ho.
- API keys, contacts, auth profiles, and runtime memory are local-only and
  excluded from Git. Never commit these files.

## 11. Browser actions in your existing browser

Browser actions existing MARK action system ka hissa hain; alag assistant ki
zaroorat nahi. Examples:

- "Chrome me YouTube kholo"
- "Current browser me search box me Python Playwright likho"
- "Page ka text padho aur screenshot save karo"

Opening aur searching normal browser me hoti hai. Click, type aur form actions
ke liye MARK pehle Chrome DevTools Protocol ke through existing Chromium
browser se attach karne ki koshish karta hai, isliye current tabs aur logged-in
session available rahte hain. Iske liye browser ko debugging port ke saath
start karein:

```bat
chrome.exe --remote-debugging-port=9222
```

Edge ke liye:

```bat
msedge.exe --remote-debugging-port=9222
```

Jo browser bina is flag ke pehle se chal raha ho, usse Chromium safe tarike se
attach nahi karne deta. Aise case me MARK persistent `.mark_profiles`
fallback use karta hai aur result ko silently successful nahi batata. Custom
DevTools endpoint ke liye `MARK_BROWSER_CDP_URL` environment variable set karein.
