# MARK Vision Department

MARK's Vision Department is embedded in the existing PySide6 desktop application. It reuses the current app lifecycle and adds a **VISION** panel; the existing camera/identity monitor remains separate. Inference is local and the model is never fetched, replaced, or retrained.

## Model validation

- Configured checkpoint: `assets/models/human.pt`.
- File size: 6,259,289 bytes.
- SHA-256: `A6AEAD7BF0ECCB35BD56731BFAA6EA19A4645A66150D2D0B19DD3FB1B116EF43`.
- Loaded with Ultralytics 8.4.212 and CPU PyTorch 2.15.0 under Python 3.11.
- Architecture/task: Ultralytics `DetectionModel`, `detect`.
- Actual class map: `{0: "Human"}`. The detector matches the configured `person` target to the model's real `Human` class; emitted events retain the model label and class index.
- Standalone positive sample inference on [Ultralytics' bus sample](https://ultralytics.com/images/bus.jpg): two detections at confidence 0.8506 and 0.8165 with bounding boxes. A project face image produced zero detections.

## Qubo camera compatibility

Qubo's official Smart Camera 360 3MP product page confirms the model, 3MP sensor, and person detection. Official support material describes viewing through the Qubo app; it does not document a public RTSP path, ONVIF, or desktop/web camera API. The official camera manual likewise describes setup and app-based live viewing, not an open LAN stream.

The optional connector uses the community [`qubopy`](https://pypi.org/project/qubopy/) client’s authenticated Qubo cloud RTSPS method. This is not an official Qubo SDK. Community documentation describes streaming for Qubo camera families, while its direct LAN P2P method is explicitly experimental and packet decoding is not verified on hardware. This project does not use that experimental LAN method, guess RTSP endpoints, or claim that the cloud connector has been tested on this specific camera/firmware. Confirm your exact device with the Qubo device list and test a live stream before relying on alerts. The cloud stream requires internet access and sends video through Qubo's stream service. See the [official Qubo product page](https://www.quboworld.com/products/smart-cam-360-3mp), [official support page](https://www.quboworld.com/pages/support-qubo-cam-3mp), and [official manual](https://www.quboworld.com/cam-360-product-information/cam-360-manual.pdf).

Phone/OTP Qubo sessions are documented by the community client as one-hour sessions without refresh tokens. Expired sessions require manual re-authentication. Do not paste OTPs, passwords, or tokens into MARK, source files, issue reports, or command-line arguments.

## Windows setup

Use the existing project's supported 64-bit Python 3.11/3.12 environment. This repository currently has Python 3.11 available; the system-wide Python 3.15 runtime is outside the existing setup's supported range.

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install -r tooling/requirements-vision.txt
```

If `.venv` already exists, activate it rather than replacing it. Install FFmpeg and ensure it is available on `PATH` if OpenCV's bundled FFmpeg cannot open the RTSPS stream. The app will display a generic error type/message and never print stream URLs or tokens.

## Manual configuration

1. If you accept the third-party integration, run `python -m vision_department.camera.authentication` from the project environment. It asks for the phone and OTP with hidden console input and uses the Qubo client’s HTTPS authentication; it stores a session in your Windows profile, outside this repository. The documented phone login session lasts about one hour. Do not put OTPs in a script or pass them on a command line.
2. Run `python -m qubopy devices` and copy the Qubo camera's device UUID into `camera.device_id` in `vision_department/config/model_config.yaml`. This project does not store your Qubo account password or OTP.
3. Confirm `model.path: assets/models/human.pt` points to the supplied checkpoint. Change confidence, interval, cooldown, target class, or retention days in the YAML as needed.
4. `recording.enabled` must remain `false`; recording is not implemented. The department only processes in-memory frames and stores event metadata in `data/vision_events.db`.
5. Start the existing desktop app, open **VISION**, and click **Start Monitoring**. The model is validated before a Qubo session or camera connection is attempted.

## Configuration defaults

`vision_department/config/model_config.yaml` sets confidence 0.45, inference interval 1 second, notification cooldown 30 seconds, retention 30 days, and recording disabled. Events continue to be stored during notification cooldown; the cooldown suppresses dashboard notifications only.

The optional API is not started by the desktop app. If needed, run `python -m uvicorn vision_department.api.app:app --host 127.0.0.1 --port 8765`; it binds only to localhost and exposes status, event history, and monitoring controls. Do not bind this API to `0.0.0.0` or expose it to a network.

## Validation and current limitations

- Model load and standalone positive/negative image inference were verified before camera work.
- Unit tests cover class-name mapping, person event formatting, event persistence/cooldown, config validation, and local-only API access.
- A real Qubo account, device UUID, camera firmware version, and live stream were not available here, so camera authentication, live frame delivery, disconnection/reconnect behavior, and physical camera performance remain unverified.
- No snapshots or continuous footage are stored. Event metadata is pruned on department startup according to `events.retention_days`.
- The dashboard is restricted by the existing desktop application's local session. FastAPI control routes are loopback-only.

## Troubleshooting

- **Model missing / invalid:** check the path and run `python -c "from ultralytics import YOLO; m=YOLO('assets/models/human.pt'); print(m.names)"`. No weights are downloaded automatically.
- **No Qubo devices:** complete Qubo app setup and authentication, check the saved Qubo client session, then retry `python -m qubopy devices`.
- **Device UUID not found:** use the UUID for this Qubo account and edit `camera.device_id`.
- **Stream unavailable:** exact model/firmware support must be confirmed with Qubo; the official docs do not promise local RTSP/ONVIF. Check internet access and session expiry. This connector uses cloud RTSPS, not a guessed LAN URL.
- **OpenCV cannot open stream:** check the OpenCV FFmpeg build and install FFmpeg on PATH. Never paste the full signed stream URL into logs or support requests.
- **Incorrect person label:** the supplied model's actual label is `Human`, index 0. The code resolves the configured `person` alias dynamically from model metadata.
