# API endpoints
MAIL_KEYS_API = "https://plus.keria.cc.cd/api/pickup/mail-keys"
MAIL_CODE_API = "https://plus.keria.cc.cd/api/pickup/mail-code"
CHATGPT_LOGIN_URL = "https://chatgpt.com/auth/login?next=%2F"
CHATGPT_SESSION_API = "https://chatgpt.com/api/auth/session"

# API timeouts (seconds)
MAIL_API_TIMEOUT = 30

# Timeouts (milliseconds)
PAGE_LOAD_TIMEOUT = 30000
EMAIL_INPUT_TIMEOUT = 30000
CODE_INPUT_TIMEOUT = 60000
LOGIN_SUCCESS_TIMEOUT = 120000

# Retry settings
CODE_FETCH_MAX_RETRIES = 3
CODE_FETCH_RETRY_DELAY = 5
LOGIN_MAX_RETRIES = 2
LOGIN_RETRY_DELAY = 2

# Browser settings
BROWSER_PROFILE_DIR = "./browser_profile"
OUTPUT_DIR = "./output"
LOG_DIR = "./logs"
DIAGNOSTICS_DIR = "./artifacts"
PROXY = None  # Browser proxy, e.g., "socks5://127.0.0.1:1080"
FLARESOLVERR_URL = "http://127.0.0.1:8191/v1"
FLARESOLVERR_MAX_TIMEOUT = 60000
FLARESOLVERR_WAIT_SECONDS = 5
EMAIL_FORM_STABILIZE_SECONDS = 30
EMAIL_POST_SUBMIT_TIMEOUT_SECONDS = 60

# Headers for API requests
API_HEADERS = {
    "accept": "application/json",
    "accept-language": "zh-CN,zh;q=0.9,en;q=0.8",
    "cache-control": "no-cache",
    "origin": "https://plus.keria.cc.cd",
    "referer": "https://plus.keria.cc.cd/",
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
}
