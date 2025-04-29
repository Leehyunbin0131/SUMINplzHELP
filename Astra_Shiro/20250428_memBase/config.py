# config.py

# --- 기존 설정 ---
# Ollama 서버 설정 (메인 LLM용)
OLLAMA_HOST = "192.168.45.160"  # Ollama 서버의 IP 주소 또는 호스트명
OLLAMA_PORT = 11434  # 기본 Ollama API 포트

# LLM 모델 설정
DEFAULT_MODEL = "gemma3:27b-it-qat"  # 기본 LLM 모델 이름
TEMPERATURE = 0.8  # 생성 온도 (0.0-1.0)
NUM_GPU = 99  # 사용할 GPU 수 (99 = 모든 사용 가능한 GPU)

# 음성-텍스트 변환 설정
STT_MODEL = "medium"  # STT 모델 크기: "tiny", "base", "small", "medium", "large"
USE_CUDA = True  # STT에 GPU 가속 사용 여부
COMPUTE_TYPE = "int8"  # GPU 가속용 계산 유형 (CUDA용 int8, CPU용 기본값)

# 오디오 녹음 설정
SILERO_SENSITIVITY = 0.7  # 음성 활동 감지 감도
MIN_RECORDING_LENGTH = 0.5  # 최소 녹음 길이(초)
POST_SPEECH_SILENCE = 1.0  # 녹음 중지를 위한 발화 후 침묵 지속 시간
EARLY_TRANSCRIPTION_SILENCE = 300  # 이 시간(ms) 이후의 침묵에서 변환 활성화

# UI 설정
SHOW_SPINNER = True  # 처리 중 스피너 표시
PRINT_TRANSCRIPTION_TIME = True  # 변환 소요 시간 출력
DEBUG_MODE = False  # 더 자세한 로깅을 위한 디버그 모드 활성화

# 네트워크 설정
REQUEST_TIMEOUT = 10  # API 요청 제한 시간(초)

# 로깅 설정
LOG_LEVEL = "INFO"  # 로깅 레벨: DEBUG, INFO, WARNING, ERROR, CRITICAL
LOG_FORMAT = "%(asctime)s - %(levelname)s - %(module)s - %(message)s"  # 로그 메시지 형식

# 향상된 로깅 설정
ENABLE_FILE_LOGGING = True  # 파일 로깅 활성화 여부
LOG_DIR = "./logs"  # 로그 디렉토리 경로
LOG_FILE_MAIN = "main.log"  # 메인 로그 파일
LOG_FILE_STT = "stt.log"  # STT 모듈 로그 파일
LOG_FILE_LLM = "llm.log"  # LLM 처리 로그 파일
LOG_FILE_MEMORY = "memory.log"  # 메모리 관련 로그 파일
LOG_MAX_SIZE = 10 * 1024 * 1024  # 각 로그 파일 최대 크기 (10MB)
LOG_BACKUP_COUNT = 5  # 보관할 로그 파일 수

# 메모리 Ollama 서버 설정 
MEM0_OLLAMA_HOST = "localhost" 
MEM0_OLLAMA_PORT = 11434       
MEM0_OLLAMA_BASE_URL = f"http://{MEM0_OLLAMA_HOST}:{MEM0_OLLAMA_PORT}" 

MEM0_LLM_MODEL = "exaone3.5:7.8b"
MEM0_EMBEDDING_MODEL = "bge-m3"

# Vector Store 설정 
VECTOR_STORE_PROVIDER = "chroma" 
CHROMA_PATH = "./chroma_db"     
CHROMA_COLLECTION = "voice_assistant_memory_chroma" 

# 메모리 사용자 ID 
MEMORY_USER_ID = "default_user"