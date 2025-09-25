# Pybrowse - a fast and private browser 
This is a python browser written in python using the PyQt6 Framework

## Download
Check the release tab. Currently only compiled for x64_86 for Windows. If you want to compile it look below:

## Compile guide:
to compile the thing for your OS and architecture please clone the repo and run the command:
```bash
git clone https://github.com/PrathamGhaywat/pybrowse-app.git
cd pybrowse-app
```
```bash
pip install -r requirements.txt
```
```bash
pip install pyinstaller
```
```bash
pyinstaller --onefile --noconsole --add-data "pybrowse_home.html;." --add-data "settings.html;." --icon=assets/favicon.ico main.py
```

## Features
1. Multi-Engine search = Use your favourite search engine(Google, Bing, DuckDuckGo, Brave, Ecosia)
2. Multi-tabs = Open as many tabs you want and close them
2. Persistent Storage
