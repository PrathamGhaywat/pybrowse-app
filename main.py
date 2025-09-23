import sys
import os
from PyQt6.QtCore import *
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import *
from PyQt6.QtWebEngineWidgets import *
from PyQt6.QtWebEngineCore import QWebEngineUrlRequestInterceptor, QWebEngineSettings

def resource_path(relative_path):
    """Get absolute path for dev and for PyInstaller bundle"""
    if hasattr(sys, '_MEIPASS'):
        # PyInstaller creates a temp folder and stores path in _MEIPASS
        return os.path.join(sys._MEIPASS, relative_path)  # type: ignore
    return os.path.join(os.path.abspath("."), relative_path)



class SearchInterceptor(QWebEngineUrlRequestInterceptor):
    def __init__(self, browser):
        super().__init__()
        self.browser = browser

    def interceptRequest(self, info):
        url = info.requestUrl().toString()
        
        # Debug: Uncomment to see all requests (for troubleshooting)
        # print(f"Request: {url}")
        
        # Only intercept Google searches from our home page and redirect to selected engine
        if "google.com/search?q=" in url and hasattr(self.browser, 'search_engine_combo'):
            current_engine = self.browser.search_engine_combo.currentText()
            if current_engine != "Google":
                # Extract the search query
                import urllib.parse
                parsed = urllib.parse.urlparse(url)
                query_params = urllib.parse.parse_qs(parsed.query)
                if 'q' in query_params:
                    search_query = query_params['q'][0]
                    new_url = self.browser.get_search_url(search_query, current_engine)
                    info.redirect(QUrl(new_url))
                    return
        
        # Block ads (currently disabled - no blocked domains)
        if self.is_ad(url):
            info.block(True)

    def is_ad(self, url):
        #domains = open("ads.txt", "r")
        blocked_domains = [
            #domains
        ]

        for domain in blocked_domains:
            if domain in url:
                return True
        return False


class Browser(QMainWindow):
    def __init__(self):
        super().__init__()
        
        # Set modern window title and properties
        self.setWindowTitle("🚀 PyBrowse - Privacy-First Browser")
        self.setMinimumSize(1200, 800)

        self.search_interceptor = SearchInterceptor(self)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.tabBarDoubleClicked.connect(self.tab_open_doubleclick)
        self.tabs.currentChanged.connect(self.current_tab_changed)
        self.tabs.setTabsClosable(True)
        self.tabs.tabCloseRequested.connect(self.close_current_tab)
        self.setCentralWidget(self.tabs)

        self.status = QStatusBar()
        self.setStatusBar(self.status)

        navbar = QToolBar()
        self.addToolBar(navbar)

        back_btn = QAction("◀", self)
        back_btn.setStatusTip("Back to previous page")
        back_btn.triggered.connect(self.navigate_back)
        navbar.addAction(back_btn)

        forward_btn = QAction("▶", self)
        forward_btn.setStatusTip("Forward to next page")
        forward_btn.triggered.connect(self.navigate_forward)
        navbar.addAction(forward_btn)

        reload_btn = QAction("🔄", self)
        reload_btn.setStatusTip("Reload page")
        reload_btn.triggered.connect(self.navigate_reload)
        navbar.addAction(reload_btn)

        home_btn = QAction("🏠", self)
        home_btn.setStatusTip("Go home")
        home_btn.triggered.connect(self.navigate_home)
        navbar.addAction(home_btn)

        self.url_bar = QLineEdit()
        self.url_bar.returnPressed.connect(self.navigate_to_url)
        navbar.addWidget(self.url_bar)

        # Search engine dropdown
        self.search_engine_combo = QComboBox()
        self.search_engine_combo.addItems(["Google", "Bing", "DuckDuckGo", "Brave", "Ecosia"])
        self.search_engine_combo.setCurrentText("Google")
        self.search_engine_combo.setToolTip("Select search engine")
        navbar.addWidget(self.search_engine_combo)

        stop_btn = QAction("⛔", self)
        stop_btn.setStatusTip("Stop loading current page")
        stop_btn.triggered.connect(self.navigate_stop)
        navbar.addAction(stop_btn)

        self.add_new_tab(None, '🚀 PyBrowse')
        
        # Apply modern styling
        self.apply_modern_styling()

        self.show()

    def add_new_tab(self, qurl=None, label="Blank"):
        if qurl is None:
            html_file = resource_path("pybrowse_home.html")
            qurl = QUrl.fromLocalFile(html_file)

        browser = QWebEngineView()
        browser.setUrl(qurl)
        
        # Set up search interceptor and security settings if page and profile are available
        page = browser.page()
        if page:
            profile = page.profile()
            if profile:
                profile.setUrlRequestInterceptor(self.search_interceptor)
                
                # Configure security settings to reduce console warnings (but allow normal browsing)
                settings = profile.settings()
                if settings:
                    # Allow JavaScript and normal navigation - only restrict dangerous features
                    settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, True)  # Ensure JS is enabled
                    settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanOpenWindows, True)  # Allow for normal navigation
                    settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanAccessClipboard, False)  # Keep restricted
                    settings.setAttribute(QWebEngineSettings.WebAttribute.AllowRunningInsecureContent, False)  # Keep secure
                    settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, False)  # Keep secure
                    settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)  # Only for our home page
                    settings.setAttribute(QWebEngineSettings.WebAttribute.LinksIncludedInFocusChain, True)  # Ensure links work
                    settings.setAttribute(QWebEngineSettings.WebAttribute.SpatialNavigationEnabled, True)  # Better navigation
                    
            # Set up console message filtering
            page.javaScriptConsoleMessage = self.filter_console_messages

        i = self.tabs.addTab(browser, label)

        self.tabs.setCurrentIndex(i)

        browser.urlChanged.connect(lambda qurl, browser=browser:
                                   self.update_urlbar(qurl, browser))

        browser.loadFinished.connect(lambda _, i=i, browser=browser:
                                     self.update_tab_title(i, browser))
                                     
        # Also update tab title when URL changes (for immediate feedback)
        browser.urlChanged.connect(lambda qurl, browser=browser, i=i:
                                  self.update_tab_title_on_url_change(i, browser, qurl))
                                  
        # Update tab title when icon changes (real favicon loaded)
        browser.iconChanged.connect(lambda icon, i=i, browser=browser:
                                   self.update_tab_title(i, browser))
                                   
        # Update tab title when page title changes
        browser.titleChanged.connect(lambda title, i=i, browser=browser:
                                    self.update_tab_title(i, browser))

    def apply_modern_styling(self):
        # Modern dark theme styling inspired by Brave browser
        self.setStyleSheet("""
            QMainWindow {
                background-color: #1a1a1a;
                color: #ffffff;
            }
            
            QTabWidget::pane {
                border: 1px solid #3a3a3a;
                background-color: #1a1a1a;
                border-radius: 8px;
            }
            
            QTabWidget::tab-bar {
                alignment: left;
            }
            
            QTabBar::tab {
                background-color: #2a2a2a;
                color: #ffffff;
                padding: 8px 16px;
                margin-right: 2px;
                border-top-left-radius: 8px;
                border-top-right-radius: 8px;
                border: 1px solid #3a3a3a;
                min-width: 120px;
            }
            
            QTabBar::tab:selected {
                background-color: #3a3a3a;
                border-bottom-color: #3a3a3a;
            }
            
            QTabBar::tab:hover {
                background-color: #4a4a4a;
            }
            
            QTabBar::close-button {
                image: url(none);
                subcontrol-position: right;
                margin: 4px;
            }
            
            QTabBar::close-button:hover {
                background-color: #ff6b6b;
                border-radius: 4px;
            }
            
            QToolBar {
                background-color: #2a2a2a;
                border: none;
                spacing: 8px;
                padding: 8px;
            }
            
            QLineEdit {
                background-color: #3a3a3a;
                color: #ffffff;
                border: 2px solid #4a4a4a;
                border-radius: 20px;
                padding: 8px 16px;
                font-size: 14px;
                min-height: 20px;
            }
            
            QLineEdit:focus {
                border-color: #ff6b35;
                background-color: #4a4a4a;
            }
            
            QComboBox {
                background-color: #3a3a3a;
                color: #ffffff;
                border: 2px solid #4a4a4a;
                border-radius: 12px;
                padding: 6px 12px;
                font-size: 12px;
                min-width: 100px;
            }
            
            QComboBox:hover {
                border-color: #ff6b35;
                background-color: #4a4a4a;
            }
            
            QComboBox::drop-down {
                border: none;
                width: 20px;
            }
            
            QComboBox::down-arrow {
                image: none;
                border-style: none;
            }
            
            QComboBox QAbstractItemView {
                background-color: #3a3a3a;
                color: #ffffff;
                selection-background-color: #ff6b35;
                border: 1px solid #4a4a4a;
                border-radius: 8px;
            }
            
            QStatusBar {
                background-color: #2a2a2a;
                color: #cccccc;
                border-top: 1px solid #3a3a3a;
            }
            
            QAction {
                color: #ffffff;
            }
        """)
        
        # Style individual buttons to look more modern
        self.style_navigation_buttons()

    def style_navigation_buttons(self):
        # Find all actions in the toolbar and style them
        toolbar = self.findChild(QToolBar)
        if toolbar:
            for action in toolbar.actions():
                # Create modern button styling
                widget = toolbar.widgetForAction(action)
                if widget and hasattr(widget, 'setStyleSheet'):
                    widget.setStyleSheet("""
                        QPushButton {
                            background-color: #3a3a3a;
                            color: #ffffff;
                            border: none;
                            border-radius: 8px;
                            padding: 8px 12px;
                            font-weight: bold;
                        }
                        
                        QPushButton:hover {
                            background-color: #ff6b35;
                        }
                        
                        QPushButton:pressed {
                            background-color: #e55a2e;
                        }
                    """)

    def tab_open_doubleclick(self, i):
        if i == -1:
            self.add_new_tab()

    def filter_console_messages(self, level, message, lineNumber, sourceID):
        """Filter out specific console warnings that are not critical"""
        # Suppress common non-critical warnings
        suppress_messages = [
            "iframe which has both allow-scripts and allow-same-origin",
            "Error with Permissions-Policy header",
            "Unrecognized feature: 'payment'",
            "Unrecognized feature: 'usb'",
            "Unrecognized feature: 'geolocation'",
            "Unrecognized feature: 'camera'",
            "Unrecognized feature: 'microphone'",
        ]
        
        # Check if this message should be suppressed
        if message:
            for suppress_pattern in suppress_messages:
                if suppress_pattern.lower() in message.lower():
                    return  # Don't log this message

    def current_tab_changed(self, i):
        current_browser = self.tabs.currentWidget()
        if current_browser and isinstance(current_browser, QWebEngineView):
            qurl = current_browser.url()
            self.update_urlbar(qurl, current_browser)
            self.update_title(current_browser)

    def close_current_tab(self, i):
        if self.tabs.count() < 2:
            return

        self.tabs.removeTab(i)

    def get_favicon_as_text(self, browser):
        """Extract favicon from browser and convert to text representation"""
        if not isinstance(browser, QWebEngineView):
            return "🌐"
            
        # Get the actual favicon from the browser
        icon = browser.icon()
        
        if not icon.isNull():
            # Try to convert favicon to a simple text representation
            # For now, we'll use a generic icon but this could be enhanced
            return "�"  # Use earth icon for sites with favicons
        
        # Fallback emoji icons for common sites (minimal set)
        url_str = browser.url().toString().lower()
        
        # Handle PyBrowse home page
        if 'pybrowse_home.html' in url_str or url_str.startswith('file://'):
            return "🚀"
            
        # Quick favicon mapping for very common sites only
        favicon_map = {
            'google.com': '🔍',
            'youtube.com': '📺', 
            'github.com': '🐙',
            'stackoverflow.com': '📚',
            'wikipedia.org': '📖',
            'reddit.com': '🤖',
            'x.com': '❌',
            'facebook.com': '📘',
            'instagram.com': '📸',
            'linkedin.com': '💼',
        }
        
        for domain, emoji in favicon_map.items():
            if domain in url_str:
                return emoji
                
        return "🌐"  # Default fallback

    def get_clean_title(self, raw_title, url):
        """Get actual website title from the loaded page"""
        url_string = url.toString()
        
        # Special handling ONLY for PyBrowse home page specifically
        if 'pybrowse_home.html' in url_string:
            return "PyBrowse Home"
        
        # Use the actual page title if available
        if raw_title and raw_title.strip():
            clean_title = raw_title.strip()
            
            # Limit excessively long titles
            if len(clean_title) > 30:
                clean_title = clean_title[:27] + "..."
                
            return clean_title
        
        # Fallback: extract domain name if no title
        try:
            from urllib.parse import urlparse
            parsed = urlparse(url_string)
            domain = parsed.netloc.replace('www.', '')
            if domain:
                return domain
            return "Loading..."
        except:
            return "New Page"

    def update_tab_title(self, tab_index, browser):
        if isinstance(browser, QWebEngineView):
            page = browser.page()
            if page:
                raw_title = page.title()
                url = browser.url()
                
                # Debug: Print what we're working with
                print(f"Debug - URL: {url.toString()}")
                print(f"Debug - Raw title: '{raw_title}'")
                
                # Get clean title - just the website name
                clean_title = self.get_clean_title(raw_title, url)
                
                print(f"Debug - Clean title: '{clean_title}'")
                
                # Set tab text to just the clean site name
                self.tabs.setTabText(tab_index, clean_title)
                
                # Clear any icons
                from PyQt6.QtGui import QIcon
                self.tabs.setTabIcon(tab_index, QIcon())

    def update_tab_title_on_url_change(self, tab_index, browser, qurl):
        """Update tab title immediately when URL changes for better UX"""
        if isinstance(browser, QWebEngineView):
            # Show loading state while page loads
            self.tabs.setTabText(tab_index, "Loading...")
            
            # Clear any icons
            from PyQt6.QtGui import QIcon
            self.tabs.setTabIcon(tab_index, QIcon())

    def update_title(self, browser):
        if browser != self.tabs.currentWidget():
            return

        if isinstance(browser, QWebEngineView):
            page = browser.page()
            if page:
                title = page.title()
                if title:
                    self.setWindowTitle(f"🚀 {title} - PyBrowse")
                else:
                    self.setWindowTitle("🚀 PyBrowse - Privacy-First Browser")

    def navigate_back(self):
        current_browser = self.tabs.currentWidget()
        if current_browser and isinstance(current_browser, QWebEngineView):
            current_browser.back()

    def navigate_forward(self):
        current_browser = self.tabs.currentWidget()
        if current_browser and isinstance(current_browser, QWebEngineView):
            current_browser.forward()

    def navigate_reload(self):
        current_browser = self.tabs.currentWidget()
        if current_browser and isinstance(current_browser, QWebEngineView):
            current_browser.reload()

    def navigate_stop(self):
        current_browser = self.tabs.currentWidget()
        if current_browser and isinstance(current_browser, QWebEngineView):
            current_browser.stop()

    def navigate_home(self):
        current_browser = self.tabs.currentWidget()
        if current_browser and isinstance(current_browser, QWebEngineView):
            html_file = resource_path("pybrowse_home.html")
            current_browser.setUrl(QUrl.fromLocalFile(html_file))

    def get_search_url(self, query, engine="Google"):
        search_engines = {
            "Google": "https://www.google.com/search?q=",
            "Bing": "https://www.bing.com/search?q=",
            "DuckDuckGo": "https://duckduckgo.com/?q=",
            "Brave": "https://search.brave.com/search?q=",
            "Ecosia": "https://www.ecosia.org/search?q="
        }
        return search_engines.get(engine, search_engines["Google"]) + query

    def perform_search(self, query, lucky=False):
        current_engine = self.search_engine_combo.currentText()
        
        if lucky and current_engine == "Google":
            # Google "I'm Feeling Lucky" search
            search_url = f"https://www.google.com/search?q={query}&btnI=1"
        else:
            search_url = self.get_search_url(query, current_engine)
        
        current_browser = self.tabs.currentWidget()
        if current_browser and isinstance(current_browser, QWebEngineView):
            current_browser.setUrl(QUrl(search_url))

    def navigate_to_url(self):
        text = self.url_bar.text().strip()
        
        # Regular URL navigation
        q = QUrl(text)
        if q.scheme() == "":
            # If it looks like a search query (no dots or starts with known patterns)
            if "." not in text or text.startswith(("what is", "how to", "why")):
                self.perform_search(text)
                return
            else:
                q.setScheme("http")
        
        current_browser = self.tabs.currentWidget()
        if current_browser and isinstance(current_browser, QWebEngineView):
            current_browser.setUrl(q)

    def update_urlbar(self, q, browser=None):
        if browser != self.tabs.currentWidget():
            return

        self.url_bar.setText(q.toString())
        self.url_bar.setCursorPosition(0)


app = QApplication(sys.argv)
QApplication.setApplicationName("PyBrowse")
QApplication.setApplicationDisplayName("🚀 PyBrowse - Privacy-First Browser")
window = Browser()
app.exec()