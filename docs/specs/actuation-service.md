# Service Specification: Actuation Service (`actuation-service`)

> **Status**: DRAFT  
> **Type**: Microservice (Edge/Local)  
> **Language**: Python 3.11  
> **Responsibility**: Controlling physical hardware (IoT devices) via GPIO or Home Assistant.

---

## 1. Business Logic & Responsibility
The `actuation-service` is the **hands** of the system. It translates high-level decisions (e.g., "Deter Intruder") into physical actions (e.g., "Turn on Floodlight for 30s"). It isolates the complex logic of *what* to do from the messy details of *how* to talk to hardware.

### Core Capabilities:
1.  **Device Abstraction**: Controls Sirens, Floodlights, Magnetic Locks, and Voice Speakers.
2.  **Protocol Management**: Supports GPIO (Raspberry Pi native), MQTT (Shelly/Sonoff devices), and Home Assistant Webhooks.
3.  **Safety Interlocks**: Prevents "flapping" (rapid on/off) and enforces maximum runtimes (e.g., siren max 2 mins).

---

## 2. Engineering Requirements

### 2.1 Inputs & Outputs
-   **Input**: Kafka `actions` topic.
-   **Output**: Physical Side Effects (Light ON), Kafka `action-logs` (confirmation).

### 2.2 Technical Stack
-   **Framework**: Python `paho-mqtt` client (listening for actions).
-   **Libraries**: `RPi.GPIO` (for direct wiring) or `requests` (for HTTP calls to smart plugs).
-   **Hardware Config**: `devices.yaml` mapping logical IDs to physical pins/topics.

---

## 3. Data Structures (Topic: `actions`)

```json
{
  "action_id": "act_101",
  "target": "siren_zone_a",
  "command": "TURN_ON",
  "duration_seconds": 30,
  "parameters": {
    "volume": 0.8
  }
}
```

---

## 4. MVP Implementation Steps
1.  **Device Map**: Create a config loader for `devices.yaml` (`siren_01` -> `GPIO_17`).
2.  **Controller**: Implement a `DeviceController` class with abstract methods (`turn_on`, `turn_off`).
3.  **Safety**: Add a wrapper ensuring no device stays on past `max_duration`.
4.  **Listener**: Consume Kafka `actions` -> lookup device -> execute command.
