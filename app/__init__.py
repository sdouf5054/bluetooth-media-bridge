# Bluetooth Media Bridge — Python Core Engine + GUI
#
# Modules:
#   bridge_engine  — Central async orchestrator
#   process_manager — bt_bridge.exe subprocess lifecycle
#   ipc_client     — TCP client for bt_bridge IPC protocol
#   smtc_manager   — Windows SMTC integration
#   config         — Persistent JSON configuration
#   tray_app       — System tray icon and menu
#   settings_window — Settings/status window
#   log_window     — Debug log viewer
#   main           — Entry point (asyncio + Qt integration)
