import requests
import json
import time
import logging
import threading
import argparse
import collections 
import os
from logging.handlers import RotatingFileHandler

try:
    import config # 설정 파일 임포트
except ModuleNotFoundError:
    print("오류: config.py 파일을 찾을 수 없습니다. OllamaChatTest.py와 같은 디렉토리에 있는지 확인하세요.")
    exit() # config 파일 없으면 실행 중지

try:
    from mem0 import Memory # mem0 임포트
except ModuleNotFoundError:
    print("오류: mem0 라이브러리를 찾을 수 없습니다. 'pip install mem0-py'로 설치해주세요.")
    exit()

try:
    # system_prompts에서 필요한 요소들을 명시적으로 임포트
    from system_prompts import (
        # MEMORY_PROMPTS, # 중요도 평가 프롬프트 더 이상 사용 안 함
        MAIN_PROMPT_TEMPLATE,
        get_astra_siro_identity_context # 새로 추가된 함수 임포트
    )
except ModuleNotFoundError:
    print("오류: system_prompts.py 파일을 찾을 수 없습니다. OllamaChatTest.py와 같은 디렉토리에 있는지 확인하세요.")
    exit()

# --- 선택적 임포트 (음성 입력용) ---
try:
    from RealtimeSTT import AudioToTextRecorder
    REALTIME_STT_AVAILABLE = True
except ModuleNotFoundError:
    REALTIME_STT_AVAILABLE = False
    # 대체 클래스 정의 (음성 입력 불가 시 사용)
    class AudioToTextRecorder:
        def __init__(self, *args, **kwargs):
            # 로거가 아직 설정되지 않았을 수 있으므로 print 사용
            print("[경고] RealtimeSTT 라이브러리가 없어 텍스트 입력 모드로 전환합니다.")
            pass
        def text(self):
            try:
                # 사용자에게 명확히 텍스트 입력임을 알림
                return input("[텍스트 입력]: ")
            except EOFError:
                 return None # 비대화형 환경에서 종료 처리
        def shutdown(self):
            pass
# --- 임포트 끝 ---

# 로그 디렉토리 생성
LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logs')
os.makedirs(LOG_DIR, exist_ok=True)

# 모듈별 로거 설정 함수
def setup_module_logger(name, log_file, level=logging.INFO):
    formatter = logging.Formatter(config.LOG_FORMAT)
    
    # 파일 핸들러 설정
    file_handler = RotatingFileHandler(
        os.path.join(LOG_DIR, log_file),
        maxBytes=5*1024*1024,  # 5MB
        backupCount=3,
        encoding='utf-8'  # UTF-8 인코딩 명시적 설정
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(level)
    
    # 스트림 핸들러는 필요 없음 (모듈별 출력 분리 목적)
    
    # 로거 설정
    logger = logging.getLogger(name)
    logger.setLevel(level)
    logger.addHandler(file_handler)
    
    # 기존 핸들러 제거 (중복 방지)
    for hdlr in logger.handlers[:]:
        if isinstance(hdlr, logging.StreamHandler) and not isinstance(hdlr, logging.FileHandler):
            logger.removeHandler(hdlr)
    
    return logger

# 메인 로거 (기본 설정)
logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL, 'INFO'),
    format=config.LOG_FORMAT
)

# 모듈별 로거 설정
main_logger = setup_module_logger('main', 'main.log')
stt_logger = setup_module_logger('stt', 'stt.log')
ltm_logger = setup_module_logger('ltm', 'ltm.log')
stm_logger = setup_module_logger('stm', 'stm.log')
llm_logger = setup_module_logger('llm', 'llm.log')

if not REALTIME_STT_AVAILABLE:
     # 로거 설정 후 경고 메시지 로깅
     stt_logger.warning("RealtimeSTT 모듈을 찾을 수 없습니다. 텍스트 입력으로만 동작합니다.")

class VoiceLLMAssistant:
    def __init__(self, ollama_host=config.OLLAMA_HOST, model=config.DEFAULT_MODEL,
                 temperature=config.TEMPERATURE, stt_model=config.STT_MODEL, use_cuda=config.USE_CUDA):
        """
        STT 및 새로운 LTM/STM 메모리 기능을 갖춘 음성 LLM 어시스턴트를 초기화합니다.
        """
        self.ollama_url = f"http://{ollama_host}:{config.OLLAMA_PORT}/api/generate"
        self.model = model
        self.temperature = temperature
        self.stt_model = stt_model
        # RealtimeSTT 없으면 use_cuda, compute_type 의미 없을 수 있음
        self.device = "cuda" if use_cuda and REALTIME_STT_AVAILABLE else "cpu"
        self.compute_type = config.COMPUTE_TYPE if use_cuda and REALTIME_STT_AVAILABLE else "default"
        self.is_processing = False
        self.processing_lock = threading.Lock()

        self.test_ollama_connection(self.ollama_url.replace('/api/generate', '/api/version'), "메인 LLM")
        self.long_term_memory = self.setup_mem0_for_ltm()
        self.short_term_memory = collections.deque(maxlen=10)
        main_logger.info("단기 기억 버퍼 (최대 10턴) 초기화 완료")

        try:
            self.setup_stt_recorder()
        except Exception as e:
            stt_logger.error(f"STT 레코더 설정 실패: {e}")
            if REALTIME_STT_AVAILABLE:
                raise RuntimeError("음성-텍스트 변환 시스템을 초기화할 수 없습니다.")

    def setup_mem0_for_ltm(self):
        """LTM 저장을 위한 mem0 Memory 인스턴스를 설정합니다."""
        ltm_logger.info("LTM 저장용 Memory 시스템 설정 중 (ChromaDB 사용)...")
        mem0_config = {
            "vector_store": {
                "provider": config.VECTOR_STORE_PROVIDER,
                "config": {
                    "collection_name": config.CHROMA_COLLECTION,
                    "path": config.CHROMA_PATH,
                },
            },
            "llm": { # 비록 중요도 평가는 안하지만, mem0 내부 다른 용도로 쓸 수 있으므로 유지
                "provider": "ollama",
                "config": {
                    "model": config.MEM0_LLM_MODEL,
                    "ollama_base_url": config.MEM0_OLLAMA_BASE_URL,
                },
            },
            "embedder": { # LTM 임베딩에 필수
                "provider": "ollama",
                "config": {
                    "model": config.MEM0_EMBEDDING_MODEL,
                    "ollama_base_url": config.MEM0_OLLAMA_BASE_URL,
                },
            },
        }
        try:
            self.test_ollama_connection(f"{config.MEM0_OLLAMA_BASE_URL}/api/version", "메모리 LLM/임베더")
            memory_instance = Memory.from_config(mem0_config)
            ltm_logger.info(f"LTM 저장용 Memory 시스템 설정 완료 (Vector Store: ChromaDB at '{config.CHROMA_PATH}', Embedder: Ollama)")
            return memory_instance
        except Exception as e:
            ltm_logger.error(f"LTM 저장용 Memory 시스템 설정 실패: {e}")
            raise RuntimeError(f"LTM Memory 시스템을 초기화할 수 없습니다: {e}")

    def test_ollama_connection(self, server_url, server_name="Ollama"):
        """지정된 Ollama 서버 URL에 연결을 시도합니다."""
        try:
            response = requests.get(server_url, timeout=config.REQUEST_TIMEOUT)
            response.raise_for_status()
            if "LLM" in server_name:
                llm_logger.info(f"{server_name} 서버에 성공적으로 연결됨 ({server_url}): {response.json()}")
            else:
                ltm_logger.info(f"{server_name} 서버에 성공적으로 연결됨 ({server_url}): {response.json()}")
        except requests.exceptions.RequestException as e:
            if "LLM" in server_name:
                llm_logger.error(f"{server_url}에 있는 {server_name} 서버에 연결할 수 없음: {e}")
            else:
                ltm_logger.error(f"{server_url}에 있는 {server_name} 서버에 연결할 수 없음: {e}")
            raise ConnectionError(f"{server_name} 서버 연결 실패. {server_url}에서 서버가 실행 중인지 확인하세요")

    def setup_stt_recorder(self):
        """RealtimeSTT 레코더 또는 대체 텍스트 입력기를 설정합니다."""
        if REALTIME_STT_AVAILABLE:
            stt_logger.info("RealtimeSTT 레코더 설정 중...")
            recorder_config = {
                "model": self.stt_model,
                "language": 'ko',
                "device": self.device,
                "compute_type": self.compute_type,
                "on_recording_start": self._on_recording_start,
                "on_recording_stop": self._on_recording_stop,
                # --- 수정된 부분 ---
                # 람다 함수가 인자 하나(audio_data)를 받도록 수정
                "on_transcription_start": lambda audio_data: stt_logger.info("오디오 변환 중..."),
                # --- 수정 끝 ---
                "enable_realtime_transcription": True,
                "on_realtime_transcription_update": self._on_realtime_update,
                "silero_sensitivity": config.SILERO_SENSITIVITY,
                "min_length_of_recording": config.MIN_RECORDING_LENGTH,
                "post_speech_silence_duration": config.POST_SPEECH_SILENCE,
                "early_transcription_on_silence": config.EARLY_TRANSCRIPTION_SILENCE,
                "spinner": config.SHOW_SPINNER,
                "print_transcription_time": config.PRINT_TRANSCRIPTION_TIME,
                "debug_mode": config.DEBUG_MODE
            }
            self.recorder = AudioToTextRecorder(**recorder_config)
            stt_logger.info("RealtimeSTT 레코더 설정 완료")
        else:
            stt_logger.info("텍스트 입력 모드로 레코더 설정 (RealtimeSTT 없음)")
            self.recorder = AudioToTextRecorder()

    def _on_recording_start(self): stt_logger.info("🎤 녹음 시작됨")
    def _on_recording_stop(self): stt_logger.info("🛑 녹음 중지됨, 변환 처리 중...")
    def _on_realtime_update(self, text): print(f"\r🎤 {text}", end="", flush=True)

    def save_to_ltm(self, conversation_text):
        """
        **수정됨:** 중요도 평가 없이 모든 대화 내용을 LTM에 저장합니다.
        백그라운드 스레드에서 실행될 수 있습니다.
        """
        thread_id = threading.get_ident()
        ltm_logger.info(f"LTM 저장 진행 중... (스레드 ID: {thread_id})")
        try:
            self.long_term_memory.add(
                conversation_text,
                user_id=config.MEMORY_USER_ID,
            )
            ltm_logger.info(f"대화 내용을 LTM에 저장했습니다: {conversation_text[:100]}... (스레드 ID: {thread_id})")
        except Exception as e:
            ltm_logger.error(f"LTM 저장 중 예상치 못한 오류 (스레드 ID: {thread_id}): {e}", exc_info=True)


    def process_voice_input(self):
        """음성 또는 텍스트 입력을 처리하고, 결과를 얻어 메인 LLM에 전송합니다."""
        if self.is_processing:
            main_logger.warning("이미 요청을 처리 중입니다. 잠시 기다려주세요")
            return
        with self.processing_lock:
            self.is_processing = True
            transcribed_text = None
            try:
                if REALTIME_STT_AVAILABLE:
                    print("\n⏳ 질문을 듣고 있습니다... (지금 말씀하세요)")
                transcribed_text = self.recorder.text()
                if not transcribed_text or transcribed_text.strip() == "":
                    if transcribed_text is None:
                         raise KeyboardInterrupt("입력 종료됨")
                    print("\n입력이 없습니다.")
                    self.is_processing = False
                    return
                input_source = "🎤 말씀하신 내용" if REALTIME_STT_AVAILABLE else "⌨️  입력하신 내용"
                print(f"\n\n{input_source}: {transcribed_text}")
                print("\n⏳ LLM 생각 중 (정체성 + STM + LTM 사용)...")
                self.send_to_llm(transcribed_text)
            except KeyboardInterrupt:
                raise
            except Exception as e:
                # 오류 발생 시에도 is_processing 플래그를 해제해야 함
                main_logger.error(f"입력 처리 중 오류 발생: {e}", exc_info=True)
                print(f"\n❌ 오류 발생: {e}")
                # self.is_processing = False # finally 블록에서 처리되므로 여기서 필요 없음
            finally:
                # 어떤 경우든 처리 완료 후 플래그 해제
                self.is_processing = False

    def send_to_llm(self, text):
        """
        동적 정체성, STM, LTM 컨텍스트와 함께 텍스트를 메인 LLM에 전송하고,
        응답 후 STM 저장 및 LTM 처리 (수정됨: LTM 무조건 저장).
        
        LTM 저장은 별도의 백그라운드 스레드에서 비동기적으로 처리됩니다.
        이를 통해 UI 응답성이 향상되며, LTM 저장이 완료되지 않아도 사용자는 계속해서
        어시스턴트와 상호작용할 수 있습니다.
        """

        stm_context = "\n".join(self.short_term_memory) if self.short_term_memory else "최근 대화 없음."
        stm_logger.debug(f"사용될 STM 컨텍스트:\n{stm_context}")

        ltm_context = "관련된 장기 기억 없음."
        try:
            ltm_search_query = text
            ltm_search_response = self.long_term_memory.search(
                query=ltm_search_query,
                user_id=config.MEMORY_USER_ID,
                limit=3
            )
            memories_found = []
            if isinstance(ltm_search_response, list):
                memories_found = ltm_search_response

            if memories_found:
                ltm_context_lines = []
                for mem in memories_found:
                    if isinstance(mem, dict):
                        memory_text = mem.get('memory', '내용 없음')
                        score = mem.get('score')
                        if score is None:
                            ltm_context_lines.append(f"- {memory_text} (관련도: N/A)")
                        else:
                            try:
                                ltm_context_lines.append(f"- {memory_text} (관련도: {float(score):.2f})")
                            except (ValueError, TypeError):
                                ltm_context_lines.append(f"- {memory_text} (관련도: {score})")
                if ltm_context_lines:
                    ltm_context = "\n".join(ltm_context_lines)

            ltm_logger.debug(f"검색된 LTM 컨텍스트:\n{ltm_context}")

        except Exception as e:
            ltm_logger.error(f"LTM 검색 중 오류 발생: {e}", exc_info=True)
            ltm_context = "장기 기억 검색 중 오류 발생."

        try:
            dynamic_identity_context = get_astra_siro_identity_context()
            llm_logger.debug(f"사용될 동적 정체성 컨텍스트:\n{dynamic_identity_context[:300]}...")
        except Exception as e:
            llm_logger.error(f"동적 정체성 컨텍스트 생성 중 오류: {e}", exc_info=True)
            dynamic_identity_context = "오류: 정체성 컨텍스트를 생성할 수 없습니다."


        try:
            prompt_with_context = MAIN_PROMPT_TEMPLATE.format(
                identity_context=dynamic_identity_context,
                short_term_memory=stm_context,
                long_term_memory=ltm_context,
                user_input=text
            )
            llm_logger.debug(f"메인 LLM에 전송될 최종 프롬프트:\n{prompt_with_context}")
        except KeyError as e:
            llm_logger.error(f"프롬프트 템플릿 포맷팅 오류: 누락된 키 - {e}")
            print(f"\n❌ 오류: 프롬프트를 구성할 수 없습니다.")
            return
        except Exception as e:
            llm_logger.error(f"프롬프트 구성 중 예상치 못한 오류: {e}", exc_info=True)
            print(f"\n❌ 오류: 프롬프트를 구성할 수 없습니다.")
            return

        payload = {
            "model": self.model,
            "prompt": prompt_with_context,
            "stream": True,
            "options": {
                "num_gpu": config.NUM_GPU,
                "temperature": self.temperature,
            }
        }
        headers = {'Content-Type': 'application/json'}
        full_response = ""

        try:
            print("\n🤖 아스트라 시로 응답:")
            response = requests.post(
                self.ollama_url, json=payload, headers=headers, stream=True,
                timeout=config.REQUEST_TIMEOUT * 6
            )
            response.raise_for_status()

            for line in response.iter_lines():
                if line:
                    decoded_line = line.decode('utf-8')
                    try:
                        json_chunk = json.loads(decoded_line)
                        response_part = json_chunk.get('response', '')
                        print(response_part, end='', flush=True)
                        full_response += response_part
                        if json_chunk.get('done', False):
                            break
                    except json.JSONDecodeError:
                        llm_logger.warning(f"응답 스트림 JSON 디코딩 오류 (무시): {decoded_line}")
                    except Exception as e:
                        llm_logger.error(f"응답 스트림 처리 중 오류: {e}", exc_info=True)

            print("\n")

            if text and full_response.strip():
                interaction_to_save = f"사용자: {text}\n아스트라 시로: {full_response}"
                self.short_term_memory.append(interaction_to_save)
                stm_logger.info("현재 대화를 STM에 추가했습니다.")
                # 비동기적으로 LTM에 저장하도록 수정
                ltm_save_thread = threading.Thread(
                    target=self.save_to_ltm,
                    args=(interaction_to_save,),
                    daemon=True
                )
                ltm_save_thread.start()
                ltm_logger.info(f"LTM 저장을 위한 백그라운드 스레드 시작됨 (ID: {ltm_save_thread.ident})")

        except requests.exceptions.Timeout:
            llm_logger.error(f"Ollama API 호출 시간 초과 ({self.ollama_url})")
            print(f"\n❌ 오류: LLM 응답 시간이 초과되었습니다.")
        except requests.exceptions.RequestException as e:
            llm_logger.error(f"Ollama API 호출 오류: {e}")
            print(f"\n❌ 오류: LLM 서버({self.ollama_url}) 응답을 받을 수 없습니다 ({e}).")
        except Exception as e:
            llm_logger.error(f"LLM 응답 처리 중 예상치 못한 오류: {e}", exc_info=True)
            print(f"\n❌ 처리 중 오류 발생: {e}")



    def run_interactive_session(self):
        """대화형 음성 또는 텍스트 세션을 실행합니다."""
        mode = "음성" if REALTIME_STT_AVAILABLE else "텍스트"
        print(f"\n🚀 {self.model} 모델과 동적 정체성을 사용하는 아스트라 시로 ({mode} 입력 모드) 어시스턴트가 준비되었습니다!")
        print("   (LTM 중요도 평가 제거됨, 모든 대화 저장)")
        print("종료하려면 Ctrl+C를 누르거나 빈 줄에서 Enter를 누르세요 (텍스트 모드).")
        print(f"각 모듈별 로그는 '{LOG_DIR}' 디렉터리에 저장됩니다.")
        
        try:
            while True:
                self.process_voice_input()
                time.sleep(0.1)
        except KeyboardInterrupt:
            print("\n\nCtrl+C 또는 입력 종료 감지됨. 어시스턴트를 종료합니다...")
        finally:
            if hasattr(self, 'recorder') and hasattr(self.recorder, 'shutdown') and callable(self.recorder.shutdown):
                try:
                    self.recorder.shutdown()
                    stt_logger.info("STT 레코더가 성공적으로 종료되었습니다.")
                except Exception as e:
                    stt_logger.error(f"STT 레코더 종료 중 오류 발생: {e}")
            print("어시스턴트가 중지되었습니다.")

def parse_arguments():
    """명령줄 인자를 파싱합니다."""
    parser = argparse.ArgumentParser(description="음성/텍스트 LLM 어시스턴트 (동적 정체성 및 LTM 중요도 평가 제거)")
    parser.add_argument("--host", type=str, default=config.OLLAMA_HOST, help=f"메인 Ollama 서버 IP/호스트명 (기본값: {config.OLLAMA_HOST} from config.py)")
    parser.add_argument("--model", type=str, default=config.DEFAULT_MODEL, help=f"사용할 메인 LLM 모델 (기본값: {config.DEFAULT_MODEL} from config.py)")
    parser.add_argument("--stt", type=str, default=config.STT_MODEL, help=f"STT 모델 크기 (기본값: {config.STT_MODEL} from config.py) (RealtimeSTT 필요)")
    parser.add_argument("--temp", type=float, default=config.TEMPERATURE, help=f"LLM 온도 (기본값: {config.TEMPERATURE} from config.py)")
    parser.add_argument("--cpu", action="store_true", default=not config.USE_CUDA, help=f"CUDA(GPU) 대신 CPU 사용 (STT용, 기본값: {'CPU' if not config.USE_CUDA else 'GPU'} from config.py, RealtimeSTT 필요)")
    parser.add_argument("--debug", action="store_true", default=config.DEBUG_MODE, help=f"자세한 로깅 활성화 (DEBUG 레벨, 기본값: {config.DEBUG_MODE} from config.py)")
    return parser.parse_args()

if __name__ == "__main__":
    args = parse_arguments()

    if args.debug:
        log_level = logging.DEBUG
        main_logger.info("명령줄 인자로 디버그 모드 활성화됨 (--debug)")
    else:
        log_level = getattr(logging, config.LOG_LEVEL.upper(), logging.INFO)

    # 모든 로거의 레벨 설정
    loggers = [main_logger, stt_logger, ltm_logger, stm_logger, llm_logger]
    for logger in loggers:
        logger.setLevel(log_level)
        for handler in logger.handlers:
            handler.setLevel(log_level)
    main_logger.info(f"모든 로거 레벨 설정됨: {logging.getLevelName(log_level)}")


    try:
        assistant = VoiceLLMAssistant(
            ollama_host=args.host, model=args.model, temperature=args.temp,
            stt_model=args.stt, use_cuda=not args.cpu
        )
        assistant.run_interactive_session()
    except ConnectionError as e:
        main_logger.critical(f"치명적 연결 오류: {e}")
        print(f"\n❌ 치명적 연결 오류: {e}")
        print(f"   Ollama 서버 ({args.host}:{config.OLLAMA_PORT} 또는 {config.MEM0_OLLAMA_BASE_URL})가 실행 중이고 접근 가능한지 확인하세요.")
    except RuntimeError as e:
        main_logger.critical(f"치명적 런타임 오류: {e}")
        print(f"\n❌ 치명적 런타임 오류: {e}")
        print("   오류 메시지를 확인하고 필요한 라이브러리 설치 및 설정을 확인하세요.")
    except ImportError as e:
        main_logger.critical(f"라이브러리 임포트 오류: {e}")
        print(f"\n❌ 치명적 오류: 필요한 라이브러리를 찾을 수 없습니다 ({e}).")
        print("   'pip install requests mem0-py' 또는 'pip install RealtimeSTT' 등으로 설치해주세요.")
    except Exception as e:
        main_logger.critical(f"어시스턴트 시작 중 예상치 못한 오류 발생: {e}", exc_info=True)
        print(f"\n❌ 예상치 못한 오류 발생: {e}")
        print("\n문제 해결을 위한 체크리스트:")
        print(f"1. 메인 Ollama 서버 ({args.host}:{config.OLLAMA_PORT}) 실행 및 접근 가능?")
        print(f"2. 메모리 Ollama 서버 ({config.MEM0_OLLAMA_BASE_URL}) 실행 및 접근 가능?")
        print(f"3. ChromaDB 경로 ('{config.CHROMA_PATH}') 접근/쓰기 권한?")
        print("4. 필요한 Python 패키지 (requests, mem0-py) 설치 확인?")
        if REALTIME_STT_AVAILABLE:
            print("   - 음성 입력 사용 시: RealtimeSTT 패키지 설치 확인?")
            print("5. 작동하는 마이크 연결? (음성 모드)")
        else:
            print("5. (RealtimeSTT 없음 - 텍스트 모드)")
        print(f"6. 지정된 모델({args.model}, {config.MEM0_LLM_MODEL}, {config.MEM0_EMBEDDING_MODEL})이 각 Ollama 서버에서 사용 가능한가요?")
        print(f"7. config.py, system_prompts.py 파일이 OllamaChatTest.py와 같은 폴더에 있나요?")