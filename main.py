import sys
import os
import sqlite3
from datetime import datetime
from PyQt6.QtCore import *
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import *
from PyQt6.QtWebEngineWidgets import *
from PyQt6.QtWebEngineCore import QWebEngineUrlRequestInterceptor, QWebEngineSettings
from PyQt6.QtWebChannel import QWebChannel

def resource_path(relative_path):
    """Get absolute path for dev and for PyInstaller bundle"""
    if hasattr(sys, '_MEIPASS'):
        # PyInstaller creates a temp folder and stores path in _MEIPASS
        return os.path.join(sys._MEIPASS, relative_path)  # type: ignore
    return os.path.join(os.path.abspath("."), relative_path)

# JavaScriptBridge removed - now using URL-based communication

class BrowserDatabase:
    """Handle SQLite database operations for browser history and sessions"""
    
    def __init__(self, db_path="pybrowse.db"):
        self.db_path = db_path
        self.init_database()
    
    def init_database(self):
        """Initialize the database with required tables"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Create history table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT NOT NULL,
                title TEXT,
                visit_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                visit_count INTEGER DEFAULT 1
            )
        ''')
        
        # Create sessions table for tab restoration
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_name TEXT DEFAULT 'default',
                tab_index INTEGER,
                url TEXT NOT NULL,
                title TEXT,
                is_current_tab BOOLEAN DEFAULT 0,
                created_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        conn.commit()
        conn.close()
    
    def add_to_history(self, url, title=""):
        """Add a URL to browsing history"""
        if not url or url.startswith('file://'):
            return  # Don't save local files or empty URLs
            
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Check if URL already exists
        cursor.execute('SELECT id, visit_count FROM history WHERE url = ?', (url,))
        existing = cursor.fetchone()
        
        if existing:
            # Update existing entry
            cursor.execute('''
                UPDATE history 
                SET visit_count = visit_count + 1, 
                    visit_time = CURRENT_TIMESTAMP,
                    title = ?
                WHERE id = ?
            ''', (title, existing[0]))
        else:
            # Insert new entry
            cursor.execute('''
                INSERT INTO history (url, title, visit_count) 
                VALUES (?, ?, 1)
            ''', (url, title))
        
        conn.commit()
        conn.close()
    
    def get_history(self, limit=100):
        """Get browsing history ordered by most recent"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT url, title, visit_time, visit_count 
            FROM history 
            ORDER BY visit_time DESC 
            LIMIT ?
        ''', (limit,))
        
        history = cursor.fetchall()
        conn.close()
        return history
    
    def save_session(self, tabs_data):
        """Save current session (open tabs)"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Clear existing session
        cursor.execute('DELETE FROM sessions WHERE session_name = ?', ('default',))
        
        # Save current tabs
        for i, (url, title, is_current) in enumerate(tabs_data):
            cursor.execute('''
                INSERT INTO sessions (session_name, tab_index, url, title, is_current_tab)
                VALUES (?, ?, ?, ?, ?)
            ''', ('default', i, url, title, is_current))
        
        conn.commit()
        conn.close()
    
    def restore_session(self):
        """Restore previous session tabs"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT tab_index, url, title, is_current_tab 
            FROM sessions 
            WHERE session_name = 'default'
            ORDER BY tab_index
        ''')
        
        session_data = cursor.fetchall()
        conn.close()
        return session_data
    
    def clear_history(self):
        """Clear all browsing history"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('DELETE FROM history')
        conn.commit()
        conn.close()



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

        # Initialize database
        self.db = BrowserDatabase()
        
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

        # Add separator and history button
        navbar.addSeparator()
        
        history_btn = QAction("📚", self)
        history_btn.setStatusTip("View browsing history")
        history_btn.triggered.connect(self.show_history)
        navbar.addAction(history_btn)

        # Apply modern styling
        self.apply_modern_styling()
        
        # Restore previous session or create new tab
        self.restore_previous_session()

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

        browser.loadFinished.connect(lambda success, i=i, browser=browser:
                                     self.on_page_load_finished(success, i, browser))
                                     
        # Track page loads for history
        browser.loadFinished.connect(lambda success, browser=browser:
                                    self.add_to_history(browser) if success else None)
                                     
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

    def on_page_load_finished(self, success, tab_index, browser):
        """Handle page load completion"""
        if success:
            # Update tab title
            self.update_tab_title(tab_index, browser)
            
            # Check if this is the settings page and populate it
            if isinstance(browser, QWebEngineView):
                url = browser.url().toString()
                if 'settings.html' in url:
                    self.populate_settings_page(browser)

    def populate_settings_page(self, browser):
        """Inject data into the settings page"""
        if not isinstance(browser, QWebEngineView):
            return
            
        # Get statistics from database
        history_data = self.db.get_history(1000)  # Get more data for stats
        session_data = self.db.restore_session()
        
        # Calculate statistics
        total_history = len(history_data)
        unique_sites = len(set(url for url, _, _, _ in history_data))
        total_visits = sum(visits for _, _, _, visits in history_data)
        current_tabs = self.tabs.count()
        
        # Prepare JavaScript to update the page
        js_code = f"""
        // Update statistics
        document.getElementById('total-history').textContent = '{total_history}';
        document.getElementById('unique-sites').textContent = '{unique_sites}';
        document.getElementById('total-visits').textContent = '{total_visits}';
        document.getElementById('current-tabs').textContent = '{current_tabs}';
        
        // Populate history
        const historyContainer = document.getElementById('history-container');
        historyContainer.innerHTML = '';
        """
        
        # Add history items (limit to 20 for performance)
        for i, (url, title, visit_time, visit_count) in enumerate(history_data[:20]):
            # Escape quotes for JavaScript
            safe_title = (title or url).replace("'", "\\'").replace('"', '\\"')
            safe_url = url.replace("'", "\\'").replace('"', '\\"')
            safe_time = visit_time.replace("'", "\\'").replace('"', '\\"')
            
            js_code += f"""
            const historyItem{i} = document.createElement('div');
            historyItem{i}.className = 'history-item';
            historyItem{i}.innerHTML = `
                <div class="history-title">{safe_title}</div>
                <div class="history-url">{safe_url}</div>
                <div class="history-meta">
                    <span>Visited: {safe_time}</span>
                    <span>{visit_count} visits</span>
                </div>
            `;
            historyItem{i}.onclick = () => navigateToUrl('{safe_url}');
            historyContainer.appendChild(historyItem{i});
            """
        
        if not history_data:
            js_code += """
            historyContainer.innerHTML = '<div class="no-history">No browsing history found</div>';
            """
        
        # Set up direct JavaScript-Python communication
        js_code += """
        // Override the clearHistory function - no confirmation dialog
        window.clearHistory = function() {
            console.log('clearHistory called - clearing immediately');
            
            // Set a flag that Python can check
            window.shouldClearHistory = true;
            
            // Show loading message
            const historyContainer = document.getElementById('history-container');
            historyContainer.innerHTML = '<div style="text-align: center; padding: 20px; color: #888;">Clearing history...</div>';
            
            // Trigger Python check
            setTimeout(function() {
                location.reload();
            }, 100);
        };
        
        // Override clearAllData function - no confirmation dialog  
        window.clearAllData = function() {
            console.log('clearAllData called - clearing immediately');
            
            // Set a flag that Python can check
            window.shouldClearAllData = true;
            
            // Show loading message
            const historyContainer = document.getElementById('history-container');
            historyContainer.innerHTML = '<div style="text-align: center; padding: 20px; color: #888;">Clearing all data...</div>';
            
            // Trigger Python check
            setTimeout(function() {
                location.reload();
            }, 100);
        };
        
        window.refreshHistory = function() {
            location.reload();
        };
        """
        
        # Execute the JavaScript and set up polling for action flags
        page = browser.page()
        if page:
            page.runJavaScript(js_code)
            
            # Set up a timer to check for JavaScript action flags
            def check_javascript_actions():
                def handle_clear_history(result):
                    if result:
                        print("Clear history flag detected, clearing history...")
                        self.clear_browser_history()
                        # Refresh the page after clearing
                        QTimer.singleShot(200, lambda: page.runJavaScript("location.reload();"))
                
                def handle_clear_all_data(result):
                    if result:
                        print("Clear all data flag detected, clearing all data...")
                        self.clear_all_browser_data()
                        # Refresh the page after clearing
                        QTimer.singleShot(200, lambda: page.runJavaScript("location.reload();"))
                
                # Check for clear history flag
                page.runJavaScript("window.shouldClearHistory || false", handle_clear_history)
                
                # Check for clear all data flag
                page.runJavaScript("window.shouldClearAllData || false", handle_clear_all_data)
            
            # Start polling every 500ms
            timer = QTimer()
            timer.timeout.connect(check_javascript_actions)
            timer.start(500)
            
            # Store timer in main browser object to prevent garbage collection
            if not hasattr(self, 'action_timers'):
                self.action_timers = []
            self.action_timers.append(timer)

    def add_to_history(self, browser):
        """Add current page to browsing history"""
        if isinstance(browser, QWebEngineView):
            url = browser.url().toString()
            page = browser.page()
            title = page.title() if page else ""
            self.db.add_to_history(url, title)

    def get_current_session_data(self):
        """Get data for all currently open tabs"""
        tabs_data = []
        current_index = self.tabs.currentIndex()
        
        for i in range(self.tabs.count()):
            browser = self.tabs.widget(i)
            if isinstance(browser, QWebEngineView):
                url = browser.url().toString()
                page = browser.page()
                title = page.title() if page else ""
                is_current = (i == current_index)
                tabs_data.append((url, title, is_current))
        
        return tabs_data

    def save_current_session(self):
        """Save current browser session to database"""
        session_data = self.get_current_session_data()
        self.db.save_session(session_data)

    def restore_previous_session(self):
        """Restore tabs from previous session"""
        session_data = self.db.restore_session()
        
        if not session_data:
            # No previous session, create default tab
            self.add_new_tab()
            return
        
        # Clear existing tabs first
        while self.tabs.count() > 0:
            self.tabs.removeTab(0)
        
        current_tab_index = 0
        # Restore each tab
        for tab_index, url, title, is_current in session_data:
            if url:
                qurl = QUrl(url)
                self.add_new_tab(qurl, title or "Restored Tab")
                if is_current:
                    current_tab_index = tab_index
        
        # Set the previously active tab
        if current_tab_index < self.tabs.count():
            self.tabs.setCurrentIndex(current_tab_index)

    def show_history(self):
        """Show browsing history in a dialog"""
        history_data = self.db.get_history(50)  # Get last 50 entries
        
        dialog = QDialog(self)
        dialog.setWindowTitle("📚 Browsing History")
        dialog.setMinimumSize(800, 600)
        
        layout = QVBoxLayout(dialog)
        
        # Create history list widget
        history_list = QListWidget()
        
        for url, title, visit_time, visit_count in history_data:
            display_title = title if title else url
            item_text = f"{display_title}\n{url}\nVisited: {visit_time} ({visit_count} times)"
            item = QListWidgetItem(item_text)
            item.setData(Qt.ItemDataRole.UserRole, url)  # Store URL for navigation
            history_list.addItem(item)
        
        # Double-click to navigate to URL
        history_list.itemDoubleClicked.connect(self.navigate_to_history_item)
        
        layout.addWidget(history_list)
        
        # Add buttons
        button_layout = QHBoxLayout()
        
        clear_btn = QPushButton("Clear History")
        clear_btn.clicked.connect(lambda: self.clear_history_and_refresh(dialog, history_list))
        button_layout.addWidget(clear_btn)
        
        button_layout.addStretch()
        
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(dialog.close)
        button_layout.addWidget(close_btn)
        
        layout.addLayout(button_layout)
        
        dialog.exec()

    def navigate_to_history_item(self, item):
        """Navigate to a URL from history"""
        url = item.data(Qt.ItemDataRole.UserRole)
        if url:
            current_browser = self.tabs.currentWidget()
            if current_browser and isinstance(current_browser, QWebEngineView):
                current_browser.setUrl(QUrl(url))

    def clear_history_and_refresh(self, dialog, history_list):
        """Clear history and refresh the dialog"""
        reply = QMessageBox.question(self, 'Clear History', 
                                   'Are you sure you want to clear all browsing history?',
                                   QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        
        if reply == QMessageBox.StandardButton.Yes:
            self.db.clear_history()
            history_list.clear()
            QMessageBox.information(self, 'History Cleared', 'Browsing history has been cleared.')

    def clear_browser_history(self):
        """Clear browser history (called from JavaScript)"""
        self.db.clear_history()
        print("Browser history cleared")

    def clear_all_browser_data(self):
        """Clear all browser data including history and sessions (called from JavaScript)"""
        # Clear history
        self.db.clear_history()
        
        # Clear sessions by saving an empty session
        self.db.save_session([])
        
        print("All browser data cleared")

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
    
    def navigate_to_settings(self):
        current_browser = self.tabs.currentWidget()
        if current_browser and isinstance(current_browser, QWebEngineView):
            settings_file = resource_path("settings.html")
            current_browser.setUrl(QUrl.fromLocalFile(settings_file))

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

    def closeEvent(self, event):
        """Save current session before closing"""
        self.save_current_session()
        event.accept()


app = QApplication(sys.argv)
QApplication.setApplicationName("PyBrowse")
QApplication.setApplicationDisplayName("🚀 PyBrowse - Privacy-First Browser")
window = Browser()
app.exec()