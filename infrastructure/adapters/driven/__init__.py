# infrastructure/adapters/driven/__init__.py
"""
Driven Adapter — 도메인이 필요로 하는 외부 자원 구현.

예정 구현체:
  visa_adapter.py     — pyvisa GPIB 장비 제어 (InstrumentPort)
  appium_adapter.py   — Appium/ADB Android 기기 제어 (DevicePort)
  excel_adapter.py    — ExcelWALManager 기반 결과 기록 (ExcelPort)
  correction_adapter.py — CorrectionManager RF 보정 (CorrectionPort)
"""
