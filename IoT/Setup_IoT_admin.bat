@echo off
rem Smart-home setup that needs administrator rights (Windows asks for permission):
rem   Install_CP210x_driver.ps1  - driver for the SONOFF Zigbee Dongle Plus MG24 (gives it a COM port):
rem                                tries Windows Update, then a CP210x*Driver*.zip from your Downloads or this folder
rem                                (silabs.com only serves browsers: get CP210x_Universal_Windows_Driver.zip there).
rem   Setup_IoT_admin.ps1        - Eclipse Mosquitto (MQTT broker) via winget.
rem Afterwards run Start_OpenAmika.bat again: the launcher finds the COM port, starts the broker and
rem zigbee2mqtt, and your Zigbee devices appear on the Tools page / in Amika's hands.
if exist "C:\Program Files\mosquitto\mosquitto.exe" (
    powershell -NoProfile -Command "Start-Process powershell -Verb RunAs -ArgumentList '-NoProfile -ExecutionPolicy Bypass -File \"%~dp0Install_CP210x_driver.ps1\"'"
) else (
    powershell -NoProfile -Command "Start-Process powershell -Verb RunAs -ArgumentList '-NoProfile -ExecutionPolicy Bypass -File \"%~dp0Setup_IoT_admin.ps1\"'"
)
