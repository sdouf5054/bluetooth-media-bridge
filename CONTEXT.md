# Bluetooth Media Bridge — Implementation Context

## 1. 프로젝트 목표

iPhone에서 Bluetooth A2DP로 음악을 Windows PC에 스트리밍할 때, Windows가 AVRCP 메타데이터(곡 제목, 아티스트, 앨범아트)를 수신하지 못하는 문제를 해결하는 독립 앱.

**핵심 역할**: iPhone → USB BT 동글(btstack) → A2DP 오디오 출력 + AVRCP 메타데이터/커버아트 수신 → Windows SMTC에 등록 → 작업표시줄 미디어 컨트롤 카드 표시 + 기존 나노리프 앱 자동 연동.

**상위 프로젝트와의 관계**: 별도의 "나노리프 Screen Mirror" PySide6 앱이 존재하며, SMTC의 focused session에서 커버아트를 가져와 LED에 반영함. Bluetooth Media Bridge가 SMTC에 메타데이터를 등록하면, 나노리프 앱은 코드 변경 없이 자동으로 iPhone 미디어를 인식함.

---

## 2. 하드웨어 환경

- **USB 동글**: TP-Link UB500 (칩셋: Realtek RTL8761B)
  - USB VID: `0x2357`, PID: `0x0604` (TP-Link 자체 VID, Realtek 기본 `0x0BDA:0x8771`이 아님)
  - Zadig로 WinUSB 드라이버 설치하여 btstack이 직접 제어
- **PC**: Windows 11 노트북 (Acer), 내장 Intel BT 어댑터 별도 보유 (btstack 사용 시 비활성화)
- **iPhone**: iPhone 17 Pro (iOS 26)
- **펌웨어 파일**: `rtl8761bu_fw`, `rtl8761bu_config` — build 폴더에 배치 필요
  - 출처: https://github.com/Elif-dot/RTL8761BU/raw/refs/heads/master/8761BU/

---

## 3. 아키텍처

```
┌─────────────────────────────────────────────┐
│         Bluetooth Media Bridge (Python)      │
│                PySide6 GUI                   │
│                                              │
│  ┌──────────┐  ┌──────────┐  ┌───────────┐  │
│  │ BT Core  │  │  Audio   │  │   SMTC    │  │
│  │ (btstack │  │  Output  │  │ Register  │  │
│  │ C process│  │(PortAudio│  │ (winsdk)  │  │
│  │ via IPC) │  │  by C)   │  │           │  │
│  └────┬─────┘  └──────────┘  └─────┬─────┘  │
│       │ TCP localhost:9876         │        │
│  ┌────┴────────────────────────────┴────┐   │
│  │         Bridge Engine (Python)        │   │
│  │  - subprocess로 bt_bridge.exe 관리    │   │
│  │  - TCP 소켓으로 JSON 이벤트 수신      │   │
│  │  - 메타데이터 → SMTC 등록             │   │
│  │  - 커버아트 JPEG → SMTC 썸네일        │   │
│  │  - 미디어키 → btstack 명령 전달       │   │
│  └───────────────────────────────────────┘   │
│                                              │
│  ┌───────────────────────────────────────┐   │
│  │              GUI Layer                │   │
│  │  - 연결 상태 / 기기 정보              │   │
│  │  - Now Playing (커버아트 + 곡 정보)   │   │
│  │  - 코덱 설정 (SBC/AAC)               │   │
│  │  - 시스템 트레이                       │   │
│  └───────────────────────────────────────┘   │
└─────────────────────────────────────────────┘
```

---

## 4. 현재 디렉토리 구조

```
C:\Users\slamt\Project\bluetooth-media-bridge\
├── bluetooth_bridge\
│   └── btstack\                          ← btstack 소스 (git clone)
│       ├── chipset\realtek\
│       │   └── btstack_chipset_realtek.c  ← PID 0x0604 엔트리 추가됨
│       ├── example\
│       │   └── a2dp_sink_demo.c           ← fopen "wb" 수정됨
│       └── port\windows-winusb\
│           ├── main.c                     ← Realtek 칩셋 강제 초기화 추가됨
│           ├── CMakeLists.txt             ← Realtek 소스 + bt_bridge 타겟 추가됨
│           ├── btstack_config.h           ← HAVE_POSIX_FILE_IO, ENABLE_AVRCP_COVER_ART 활성
│           ├── bt_bridge.c                ← ★ 핵심 파일 — IPC 서버 + 자동화
│           └── build\
│               ├── bt_bridge.exe          ← 빌드 산출물
│               ├── rtl8761bu_fw           ← Realtek 펌웨어
│               ├── rtl8761bu_config       ← Realtek config
│               └── cover.jpg             ← 최근 다운로드된 커버아트
├── app\                                   ← (미구현) Python GUI 앱
└── CONTEXT.md                             ← 이 파일
```

---

## 5. btstack 소스에 가한 수정사항 (원본 대비 diff)

### 5.1 `chipset/realtek/btstack_chipset_realtek.c`
- TP-Link UB500의 VID/PID (`0x2357:0x0604`)를 RTL8761BU 엔트리로 추가
- 원본에는 `0x0BDA:0x8771`만 등록돼 있었음

### 5.2 `port/windows-winusb/main.c`
- `#include "btstack_chipset_realtek.h"` 추가
- `#include "bluetooth_company_id.h"` 추가
- `hci_init()` 직후에 Realtek 칩셋 강제 설정:
  ```c
  btstack_chipset_realtek_set_product_id(0x0604);
  hci_set_chipset(btstack_chipset_realtek_instance());
  hci_enable_custom_pre_init();
  ```
- Realtek USB 컨트롤러 목록 등록 + TP-Link VID/PID 수동 추가:
  ```c
  hci_transport_usb_add_device(0x2357, 0x0604);
  ```

### 5.3 `port/windows-winusb/CMakeLists.txt`
- `include_directories(../../chipset/realtek)` 추가
- `file(GLOB SOURCES_REALTEK "../../chipset/realtek/*.c")` 추가
- `${SOURCES_REALTEK}`을 SOURCES에 추가
- `bt_bridge` 타겟 추가: `add_executable(bt_bridge ...)`, `target_link_libraries(bt_bridge btstack setupapi winusb ws2_32)`

### 5.4 `example/a2dp_sink_demo.c`
- `fopen(..., "w")` → `fopen(..., "wb")` 변경 (Windows에서 JPEG 바이너리 깨짐 방지)

---

## 6. bt_bridge.c — 핵심 파일 상세

`a2dp_sink_demo.c`를 기반으로 작성. 주요 추가/변경:

### 6.1 IPC 서버 (Winsock TCP)
- 포트 9876, localhost only, non-blocking
- 최대 4 클라이언트 동시 접속
- btstack run loop에 50ms 폴링 타이머로 통합 (`ipc_poll_timer_handler`)
- 뉴라인 구분 JSON 프로토콜

### 6.2 IPC 프로토콜

**bt_bridge → Python (JSON, newline-delimited)**:
```json
{"type":"ready","addr":"B8:FB:B3:FA:85:F6"}
{"type":"connected","addr":"68:EF:DC:CE:8C:F9"}
{"type":"disconnected"}
{"type":"metadata","title":"Solo","artist":"Frank Ocean","album":"Blonde","genre":"R&B","cover_art_handle":"1000013","track_id":5}
{"type":"playback","status":"playing"}
{"type":"playback","status":"paused"}
{"type":"volume","percent":80,"raw":102}
{"type":"cover_art","size":34717,"track_id":5}
  → 이 JSON 직후에 size 바이트의 JPEG 바이너리가 전송됨
{"type":"stream_started"}
{"type":"stream_stopped"}
```

**Python → bt_bridge (JSON, newline-delimited)**:
```json
{"cmd":"play"}
{"cmd":"pause"}
{"cmd":"stop"}
{"cmd":"next"}
{"cmd":"prev"}
{"cmd":"volume_up"}
{"cmd":"volume_down"}
{"cmd":"get_metadata"}
```

### 6.3 자동화 로직
- **TRACK_CHANGED** → 자동으로 `get_now_playing_info` 호출
- **COVER_ART_INFO 수신** → 이전 title/artist와 비교하여 실제 트랙 변경 시에만:
  - IPC로 메타데이터 전송
  - 커버아트 자동 다운로드 (`cover.jpg` 파일 저장 + IPC 바이너리 전송)
- **디듀핑**: `prev_title`, `prev_artist`, `prev_image_handle`로 중복 방지. `current_track_id`로 stale cover art 방지.

### 6.4 GAP 설정
- 디바이스 이름: `"Bluetooth Media Bridge 00:00:00:00:00:00"` (MAC 자동 치환)
- Discoverable: 활성
- Class of Device: `0x200404` (Audio, Headphone)
- Page Scan + Inquiry Scan 활성 → iPhone에서 검색 가능

### 6.5 알려진 이슈
- 커버아트 다운로드 타이밍: iPhone이 TRACK_CHANGED를 2~3회 중복 전송하는 경우 있음. 디듀핑 로직으로 대부분 처리되나, 간헐적으로 이전 곡 커버아트가 잠깐 표시될 수 있음.
- YouTube Music 등 일부 앱에서 아티스트 필드에 채널명이 오는 경우 있음 (예: "Blonded" instead of "Frank Ocean").
- Cover Art BIP/OBEX 다운로드가 간헐적으로 실패 시 `cover.jpg`가 갱신 안 됨 → iTunes Search API fallback 미구현.

---

## 7. 빌드 환경

- **MSYS2 MinGW 64-bit** 셸에서 빌드
- 필요 패키지: `git cmake make mingw-w64-x86_64-toolchain mingw-w64-x86_64-portaudio python`
- 빌드 명령:
  ```bash
  cd btstack/port/windows-winusb/build
  cmake ..
  make bt_bridge
  ```
- 실행 시 build 폴더에 `rtl8761bu_fw`, `rtl8761bu_config` 필요

---

## 8. 구현 로드맵 (5턴 계획)

### 턴 1 ✅ 완료: bt_bridge.c + IPC 서버
- TCP 소켓 서버, 자동 메타데이터/커버아트, JSON IPC 프로토콜
- 빌드 + 실행 + IPC 연결 테스트 완료

### 턴 2 🔜: Python 코어 엔진
- `process_manager.py` — bt_bridge.exe subprocess 시작/종료/재시작
- `ipc_client.py` — TCP 소켓 클라이언트, JSON 파싱, 커버아트 바이너리 수신
- `bridge_engine.py` — 이벤트 버스, 컴포넌트 연결
- CLI 테스트로 메타데이터 수신 검증

### 턴 3: SMTC 등록
- `smtc_manager.py` — `winsdk` 패키지로 SMTC 세션 생성
- 메타데이터/커버아트/재생상태 → SMTC DisplayUpdater
- 미디어 키 이벤트 수신 → IPC로 btstack에 명령 전달
- 작업표시줄 미디어 컨트롤 카드 동작 확인

### 턴 4: GUI + 트레이
- PySide6 메인 윈도우
- 연결 상태 패널, Now Playing (커버아트 + 곡 정보)
- 코덱 설정 UI (SBC/AAC 선택)
- 시스템 트레이 (최소화, 시작프로그램 등록)

### 턴 5: 통합 + 마무리
- main.py 진입점
- 에러 핸들링, 재연결 로직
- sleep/wake USB 복구
- config 저장/복원
- README

---

## 9. Windows SMTC 관련 설계 결정

- Windows SMTC는 여러 미디어 세션을 동시에 관리하며, `GetFocusedSession()`으로 현재 포커스된 세션을 반환
- PC에서 YouTube + iPhone에서 음악 동시 재생 시, 가장 최근에 재생 상태가 바뀐 세션이 focused session이 됨
- 나노리프 앱은 focused session의 커버아트를 사용하므로, btstack 앱이 SMTC에만 등록하면 자동 연동

---

## 10. 파일 첨부 가이드 (다른 대화에서 사용 시)

다른 대화에서 구현을 이어갈 때는 이 CONTEXT.md와 함께 다음 파일을 첨부:

**필수 파일**:
1. `bt_bridge.c` — C 쪽 핵심 파일
2. 이 `CONTEXT.md`

**해당 턴에서 작업할 파일들**:
- 턴 2: (신규 생성이므로 첨부 불필요)
- 턴 3: `smtc_manager.py` (신규)
- 턴 4: UI 파일들 (신규)
- 턴 5: 전체 통합 시 기존 파일들

**C 쪽 수정 필요 시 추가 첨부**:
- `main.c`, `CMakeLists.txt`, `btstack_config.h`
