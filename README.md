# Pybrowse - a fast and private browser 
This is a python browser written in python using the PyQt6 Framework

## Download
Check the release tab. Currently only compiled for x64_86 for Windows. If you want to compile it look below:

## Compile guide:
to compile the thing for your OS and architecture please clonethe repo and run the command:
```bash
pip install -r requirements.txt
```
```bash
pip install pyinstaller
```
```bash
pyinstaller --onefile --noconsole --add-data "pybrowse_home.html;." main.py
```

## Features
1. Multi-Engine search = Use your favourite search engine(Google, Bing, DuckDuckGo, Brave, Ecosia)
2. Multi-tabs = Open as many tabs you want and close them
3. Move your url bar whaere ever you want
## Future features(planned):
1. Tab moving = Move your tabs wherever you want
2. User defined CSS
3. Ad and tracker blocker = No more ads
4. Camera, GPS, Devconsole, ... = Suppor tnormal browser access requests
5. Persistent Storage and proper cookie handling = Pretty self-eplanatory


