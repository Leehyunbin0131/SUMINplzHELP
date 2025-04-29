#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import sys
import time
import json
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox, filedialog, font
from tkinter.ttk import Notebook
import threading
import queue
import logging
import importlib.util
import traceback
import requests

# --- 기존 모듈 임포트 ---
try:
    import config
except ModuleNotFoundError:
    messagebox.showerror("모듈 오류", "config.py 파일을 찾을 수 없습니다. AstraUI.py와 같은 디렉토리에 있는지 확인하세요.")
    sys.exit(1)

try:
    from system_prompts import get_astra_siro_identity_context
except ModuleNotFoundError:
    messagebox.showerror("모듈 오류", "system_prompts.py 파일을 찾을 수 없습니다. AstraUI.py와 같은 디렉토리에 있는지 확인하세요.")
    sys.exit(1)

# 동적으로 OllamaChatTest 모듈 임포트 시도
try:
    spec = importlib.util.spec_from_file_location("OllamaChatTest", "OllamaChatTest.py")
    ollama_chat_test = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ollama_chat_test)
    # OllamaChatTest에서 필요한 클래스와 함수 가져오기
    VoiceLLMAssistant = ollama_chat_test.VoiceLLMAssistant
except (ModuleNotFoundError, FileNotFoundError):
    messagebox.showerror("모듈 오류", "OllamaChatTest.py 파일을 찾을 수 없습니다. AstraUI.py와 같은 디렉토리에 있는지 확인하세요.")
    sys.exit(1)
except Exception as e:
    messagebox.showerror("모듈 오류", f"OllamaChatTest.py 로딩 중 오류 발생: {e}")
    sys.exit(1)

# mem0 라이브러리 확인
try:
    import mem0
    MEM0_AVAILABLE = True
except ImportError:
    MEM0_AVAILABLE = False

# RealtimeSTT 라이브러리 확인
try:
    from RealtimeSTT import AudioToTextRecorder
    REALTIME_STT_AVAILABLE = True
except ImportError:
    REALTIME_STT_AVAILABLE = False


class LogHandler(logging.Handler):
    """GUI 로그 핸들러 - 로그 메시지를 큐로 전달"""
    def __init__(self, log_queue):
        super().__init__()
        self.log_queue = log_queue
        
    def emit(self, record):
        try:
            msg = self.format(record)
            self.log_queue.put((record.levelname, msg))
        except Exception:
            self.handleError(record)


class AstraUI(tk.Tk):
    """아스트라 시로 통합 GUI 클래스"""
    
    def __init__(self):
        super().__init__()
        
        # 기본 창 설정
        self.title("아스트라 시로 - 통합 제어 UI")
        self.geometry("1200x800")
        self.protocol("WM_DELETE_WINDOW", self.on_closing)
        
        # 로깅 설정
        self.log_queue = queue.Queue()
        self.setup_logging()
        
        # 상태 변수
        self.is_assistant_ready = False
        self.is_processing = False
        self.assistant = None
        self.assistant_thread = None
        
        # 실행 디렉토리 확인
        if not os.path.isfile("config.py") or not os.path.isfile("system_prompts.py"):
            messagebox.showwarning("설정 파일 경고", 
                                  "필요한 설정 파일을 찾을 수 없습니다.\n프로그램을 적절한 디렉토리에서 실행하세요.")
        
        # UI 구성
        self.create_menu()
        self.create_main_frame()
        self.create_status_bar()
        
        # UI 업데이트 타이머 시작
        self.update_ui_timer()
        
        # 설정 로드 및 어시스턴트 초기화
        self.load_config()
        self.init_assistant()

    def setup_logging(self):
        """GUI 로깅 설정"""
        # 루트 로거 구성
        root_logger = logging.getLogger()
        root_logger.setLevel(getattr(logging, config.LOG_LEVEL, logging.INFO))
        
        # GUI 로그 핸들러 추가
        gui_handler = LogHandler(self.log_queue)
        gui_handler.setFormatter(logging.Formatter(config.LOG_FORMAT))
        root_logger.addHandler(gui_handler)
        
        # 1초마다 로그 큐 처리
        self.after(1000, self.process_log_queue)

    def process_log_queue(self):
        """로그 큐에서 메시지를 가져와 로그 창에 표시"""
        try:
            while True:
                level, message = self.log_queue.get_nowait()
                
                # 로그 레벨에 따른 색상 설정
                tag = None
                if level == "ERROR" or level == "CRITICAL":
                    tag = "error"
                elif level == "WARNING":
                    tag = "warning"
                elif level == "INFO":
                    tag = "info"
                elif level == "DEBUG":
                    tag = "debug"
                
                # 로그 창에 메시지 추가
                self.logs_text.insert(tk.END, message + "\n", tag)
                self.logs_text.see(tk.END)
                
                # 태그 설정
                if tag:
                    self.logs_text.tag_config("error", foreground="red")
                    self.logs_text.tag_config("warning", foreground="orange")
                    self.logs_text.tag_config("info", foreground="green")
                    self.logs_text.tag_config("debug", foreground="gray")
                
                self.log_queue.task_done()
        except queue.Empty:
            pass
        finally:
            # 재귀 호출로 계속 처리
            self.after(1000, self.process_log_queue)

    def create_menu(self):
        """메뉴바 생성"""
        menubar = tk.Menu(self)
        
        # 파일 메뉴
        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="대화 내역 저장", command=self.save_conversation)
        file_menu.add_command(label="설정 열기...", command=self.open_config_file)
        file_menu.add_separator()
        file_menu.add_command(label="종료", command=self.on_closing)
        menubar.add_cascade(label="파일", menu=file_menu)
        
        # 설정 메뉴
        settings_menu = tk.Menu(menubar, tearoff=0)
        settings_menu.add_command(label="모델 변경...", command=self.change_model)
        settings_menu.add_command(label="AI 온도 조정...", command=self.change_temperature)
        settings_menu.add_command(label="메모리 관리...", command=self.manage_memory)
        menubar.add_cascade(label="설정", menu=settings_menu)
        
        # 도움말 메뉴
        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(label="프로그램 정보", command=self.show_about)
        help_menu.add_command(label="시스템 상태", command=self.show_system_status)
        menubar.add_cascade(label="도움말", menu=help_menu)
        
        self.config(menu=menubar)

    def create_main_frame(self):
        """메인 프레임 UI 구성"""
        # 메인 프레임 생성
        self.main_frame = ttk.Frame(self)
        self.main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # 수평 패널 분할 (PanedWindow)
        self.main_paned = ttk.PanedWindow(self.main_frame, orient=tk.HORIZONTAL)
        self.main_paned.pack(fill=tk.BOTH, expand=True)
        
        # 왼쪽 패널 (채팅)
        self.chat_frame = ttk.Frame(self.main_paned)
        self.main_paned.add(self.chat_frame, weight=3)
        
        # 오른쪽 패널 (탭)
        self.right_frame = ttk.Frame(self.main_paned)
        self.main_paned.add(self.right_frame, weight=2)
        
        # 왼쪽 패널 구성 (채팅)
        self.setup_chat_frame()
        
        # 오른쪽 패널 구성 (메모리 및 로그 탭)
        self.setup_right_tabs()
        
        # 메인 패널 가중치 설정 (오류 방지를 위해 코드 제거)
        # 최소 크기는 직접 프레임에 설정
        self.chat_frame.configure(width=400)
        self.right_frame.configure(width=300)

    def setup_chat_frame(self):
        """채팅 프레임 설정"""
        # 대화 표시 영역
        self.conversation_frame = ttk.LabelFrame(self.chat_frame, text="대화")
        self.conversation_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # 대화 텍스트 영역
        self.conversation_text = scrolledtext.ScrolledText(
            self.conversation_frame, wrap=tk.WORD, font=("맑은 고딕", 10)
        )
        self.conversation_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.conversation_text.config(state=tk.DISABLED)  # 읽기 전용
        
        # 태그 설정
        self.conversation_text.tag_config("user", foreground="blue")
        self.conversation_text.tag_config("assistant", foreground="green")
        self.conversation_text.tag_config("system", foreground="gray", font=("맑은 고딕", 9, "italic"))
        
        # 입력 영역 프레임
        input_frame = ttk.Frame(self.chat_frame)
        input_frame.pack(fill=tk.X, padx=5, pady=5)
        
        # 전송 버튼 프레임
        button_frame = ttk.Frame(input_frame)
        button_frame.pack(fill=tk.X, side=tk.BOTTOM)
        
        # 음성 토글 버튼
        self.voice_status_label = ttk.Label(
            button_frame, text="🎤 음성 인식 준비됨" if REALTIME_STT_AVAILABLE else "🎤 음성 인식 불가 (RealtimeSTT 없음)"
        )
        self.voice_status_label.pack(side=tk.LEFT, padx=5)
        
        # 클리어 버튼
        self.clear_button = ttk.Button(button_frame, text="대화 지우기", command=self.clear_conversation)
        self.clear_button.pack(side=tk.LEFT, padx=5)
        
        # 전송 버튼
        self.voice_button = ttk.Button(
            button_frame, 
            text="음성 인식 시작", 
            command=self.toggle_voice_recognition,
            state=tk.DISABLED if not REALTIME_STT_AVAILABLE else tk.NORMAL
        )
        self.voice_button.pack(side=tk.RIGHT, padx=5)
        
        # 현재 처리 중 표시 (프로그레스 바)
        self.progress_var = tk.DoubleVar()
        self.progress_bar = ttk.Progressbar(
            self.chat_frame, orient=tk.HORIZONTAL, mode="indeterminate", variable=self.progress_var
        )
        self.progress_bar.pack(fill=tk.X, padx=5, pady=5)
        self.progress_bar.pack_forget()  # 초기에는 숨김

        # 음성 인식 상태 변수
        self.is_voice_active = False

    def setup_right_tabs(self):
        """오른쪽 탭 패널 설정 (메모리, 로그)"""
        # 탭 컨트롤 생성
        self.tabs = ttk.Notebook(self.right_frame)
        self.tabs.pack(fill=tk.BOTH, expand=True)
        
        # 탭 1: 단기 기억 (STM)
        self.stm_frame = ttk.Frame(self.tabs)
        self.tabs.add(self.stm_frame, text="단기 기억 (STM)")
        
        # STM 텍스트 영역
        self.stm_text = scrolledtext.ScrolledText(
            self.stm_frame, wrap=tk.WORD, font=("맑은 고딕", 9)
        )
        self.stm_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.stm_text.config(state=tk.DISABLED)  # 읽기 전용
        
        # 탭 2: 장기 기억 (LTM)
        self.ltm_frame = ttk.Frame(self.tabs)
        self.tabs.add(self.ltm_frame, text="장기 기억 (LTM)")
        
        # LTM 검색 프레임
        ltm_search_frame = ttk.Frame(self.ltm_frame)
        ltm_search_frame.pack(fill=tk.X, padx=5, pady=5)
        
        ttk.Label(ltm_search_frame, text="기억 검색:").pack(side=tk.LEFT, padx=5)
        
        self.ltm_search_entry = ttk.Entry(ltm_search_frame)
        self.ltm_search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        self.ltm_search_entry.bind("<Return>", self.search_ltm)
        
        self.ltm_search_button = ttk.Button(ltm_search_frame, text="검색", command=self.search_ltm)
        self.ltm_search_button.pack(side=tk.RIGHT, padx=5)
        
        # LTM 텍스트 영역
        self.ltm_text = scrolledtext.ScrolledText(
            self.ltm_frame, wrap=tk.WORD, font=("맑은 고딕", 9)
        )
        self.ltm_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.ltm_text.config(state=tk.DISABLED)  # 읽기 전용
        
        # 탭 3: 설정
        self.settings_frame = ttk.Frame(self.tabs)
        self.tabs.add(self.settings_frame, text="설정")
        
        # 설정 스크롤 영역
        settings_canvas = tk.Canvas(self.settings_frame)
        settings_scrollbar = ttk.Scrollbar(self.settings_frame, orient="vertical", command=settings_canvas.yview)
        settings_scrollable_frame = ttk.Frame(settings_canvas)
        
        settings_scrollable_frame.bind(
            "<Configure>",
            lambda e: settings_canvas.configure(scrollregion=settings_canvas.bbox("all"))
        )
        
        settings_canvas.create_window((0, 0), window=settings_scrollable_frame, anchor="nw")
        settings_canvas.configure(yscrollcommand=settings_scrollbar.set)
        
        settings_canvas.pack(side="left", fill="both", expand=True)
        settings_scrollbar.pack(side="right", fill="y")
        
        # 설정 그룹
        self.setup_settings_controls(settings_scrollable_frame)
        
        # 탭 4: 로그
        self.logs_frame = ttk.Frame(self.tabs)
        self.tabs.add(self.logs_frame, text="로그")
        
        # 로그 텍스트 영역
        self.logs_text = scrolledtext.ScrolledText(
            self.logs_frame, wrap=tk.WORD, font=("Consolas", 9)
        )
        self.logs_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.logs_text.config(state=tk.DISABLED)
        
        # 로그 컨트롤 프레임
        logs_control_frame = ttk.Frame(self.logs_frame)
        logs_control_frame.pack(fill=tk.X, padx=5, pady=5)
        
        # 로그 레벨 선택
        ttk.Label(logs_control_frame, text="로그 레벨:").pack(side=tk.LEFT, padx=5)
        
        self.log_level_var = tk.StringVar(value=config.LOG_LEVEL)
        log_level_combo = ttk.Combobox(
            logs_control_frame, textvariable=self.log_level_var,
            values=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        )
        log_level_combo.pack(side=tk.LEFT, padx=5)
        log_level_combo.bind("<<ComboboxSelected>>", self.change_log_level)
        
        # 로그 지우기 버튼
        ttk.Button(logs_control_frame, text="로그 지우기", command=self.clear_logs).pack(side=tk.RIGHT, padx=5)

    def setup_settings_controls(self, parent_frame):
        """설정 탭 내부 컨트롤 설정"""
        # LLM 설정 그룹
        llm_frame = ttk.LabelFrame(parent_frame, text="LLM 설정")
        llm_frame.pack(fill=tk.X, padx=5, pady=5, ipady=5)
        
        # 모델 설정
        ttk.Label(llm_frame, text="모델:").grid(row=0, column=0, sticky="w", padx=5, pady=2)
        self.model_var = tk.StringVar(value=config.DEFAULT_MODEL)
        ttk.Entry(llm_frame, textvariable=self.model_var, width=30).grid(row=0, column=1, sticky="ew", padx=5, pady=2)
        ttk.Button(llm_frame, text="변경", command=self.apply_model_change).grid(row=0, column=2, padx=5, pady=2)
        
        # 온도 설정
        ttk.Label(llm_frame, text="온도:").grid(row=1, column=0, sticky="w", padx=5, pady=2)
        self.temperature_var = tk.DoubleVar(value=config.TEMPERATURE)
        temp_scale = ttk.Scale(
            llm_frame, from_=0.0, to=1.0, orient="horizontal",
            variable=self.temperature_var, length=200
        )
        temp_scale.grid(row=1, column=1, sticky="ew", padx=5, pady=2)
        temp_scale.bind("<ButtonRelease-1>", self.update_temp_label)
        
        self.temp_label = ttk.Label(llm_frame, text=f"{config.TEMPERATURE:.2f}")
        self.temp_label.grid(row=1, column=2, padx=5, pady=2)
        
        # Ollama 서버 설정
        ttk.Label(llm_frame, text="Ollama 서버:").grid(row=2, column=0, sticky="w", padx=5, pady=2)
        
        server_frame = ttk.Frame(llm_frame)
        server_frame.grid(row=2, column=1, sticky="ew", padx=5, pady=2)
        
        self.ollama_host_var = tk.StringVar(value=config.OLLAMA_HOST)
        ttk.Entry(server_frame, textvariable=self.ollama_host_var, width=15).pack(side=tk.LEFT, fill=tk.X, expand=True)
        
        ttk.Label(server_frame, text=":").pack(side=tk.LEFT)
        
        self.ollama_port_var = tk.IntVar(value=config.OLLAMA_PORT)
        ttk.Entry(server_frame, textvariable=self.ollama_port_var, width=6).pack(side=tk.LEFT)
        
        ttk.Button(llm_frame, text="연결 테스트", command=self.test_ollama_connection).grid(row=2, column=2, padx=5, pady=2)
        
        # 음성 설정 그룹
        voice_frame = ttk.LabelFrame(parent_frame, text="음성 설정")
        voice_frame.pack(fill=tk.X, padx=5, pady=5, ipady=5)
        
        # 음성 인식 상태
        ttk.Label(voice_frame, text="음성 인식:").grid(row=0, column=0, sticky="w", padx=5, pady=2)
        self.stt_status_label = ttk.Label(
            voice_frame,
            text="사용 가능" if REALTIME_STT_AVAILABLE else "사용 불가 (RealtimeSTT 없음)"
        )
        self.stt_status_label.grid(row=0, column=1, sticky="w", padx=5, pady=2)
        
        # STT 모델 설정
        ttk.Label(voice_frame, text="STT 모델:").grid(row=1, column=0, sticky="w", padx=5, pady=2)
        self.stt_model_var = tk.StringVar(value=config.STT_MODEL)
        stt_model_combo = ttk.Combobox(
            voice_frame, textvariable=self.stt_model_var,
            values=["tiny", "base", "small", "medium", "large"], 
            state="readonly" if REALTIME_STT_AVAILABLE else "disabled"
        )
        stt_model_combo.grid(row=1, column=1, sticky="ew", padx=5, pady=2)
        
        # 메모리 설정 그룹
        memory_frame = ttk.LabelFrame(parent_frame, text="메모리 설정")
        memory_frame.pack(fill=tk.X, padx=5, pady=5, ipady=5)
        
        # LTM 상태
        ttk.Label(memory_frame, text="장기 기억:").grid(row=0, column=0, sticky="w", padx=5, pady=2)
        self.ltm_status_label = ttk.Label(
            memory_frame,
            text="사용 가능" if MEM0_AVAILABLE else "사용 불가 (mem0 없음)"
        )
        self.ltm_status_label.grid(row=0, column=1, sticky="w", padx=5, pady=2)
        
        # 메모리 관리 버튼
        ttk.Button(memory_frame, text="메모리 관리", command=self.manage_memory).grid(row=0, column=2, padx=5, pady=2)
        
        # STM 크기 설정
        ttk.Label(memory_frame, text="STM 크기:").grid(row=1, column=0, sticky="w", padx=5, pady=2)
        self.stm_size_var = tk.IntVar(value=10)  # 기본값은 10
        ttk.Spinbox(
            memory_frame, from_=1, to=20, textvariable=self.stm_size_var, width=5
        ).grid(row=1, column=1, sticky="w", padx=5, pady=2)
        
        # 적용 버튼
        ttk.Button(parent_frame, text="모든 설정 적용", command=self.apply_settings).pack(padx=5, pady=10)

    def create_status_bar(self):
        """상태 표시줄 생성"""
        status_frame = ttk.Frame(self)
        status_frame.pack(side=tk.BOTTOM, fill=tk.X)
        
        # 왼쪽 상태 레이블
        self.status_label = ttk.Label(status_frame, text="초기화 중...")
        self.status_label.pack(side=tk.LEFT, padx=5, pady=2)
        
        # 구분선
        ttk.Separator(status_frame, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=5, pady=2)
        
        # 모델 상태
        self.model_label = ttk.Label(status_frame, text=f"모델: {config.DEFAULT_MODEL}")
        self.model_label.pack(side=tk.LEFT, padx=5, pady=2)
        
        # 구분선
        ttk.Separator(status_frame, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=5, pady=2)
        
        # 메모리 상태
        self.memory_label = ttk.Label(status_frame, text="메모리: 초기화 중")
        self.memory_label.pack(side=tk.LEFT, padx=5, pady=2)

    def load_config(self):
        """설정 파일에서 설정 로드"""
        try:
            # 설정값 UI에 반영
            self.model_var.set(config.DEFAULT_MODEL)
            self.temperature_var.set(config.TEMPERATURE)
            self.temp_label.config(text=f"{config.TEMPERATURE:.2f}")
            self.ollama_host_var.set(config.OLLAMA_HOST)
            self.ollama_port_var.set(config.OLLAMA_PORT)
            self.stt_model_var.set(config.STT_MODEL)
            self.log_level_var.set(config.LOG_LEVEL)
            
            # 상태 표시 업데이트
            self.model_label.config(text=f"모델: {config.DEFAULT_MODEL}")
            logging.info("설정 로드 완료")
        except Exception as e:
            logging.error(f"설정 로드 중 오류: {e}")
            messagebox.showerror("설정 오류", f"설정 로드 중 오류가 발생했습니다: {e}")

    def init_assistant(self):
        """어시스턴트 초기화 (별도 스레드에서 실행)"""
        if self.assistant is not None:
            return  # 이미 초기화되어 있음
        
        def initialize_worker():
            try:
                self.update_status("어시스턴트 초기화 중...")
                self.assistant = VoiceLLMAssistant(
                    ollama_host=config.OLLAMA_HOST,
                    model=config.DEFAULT_MODEL,
                    temperature=config.TEMPERATURE,
                    stt_model=config.STT_MODEL,
                    use_cuda=config.USE_CUDA
                )
                self.is_assistant_ready = True
                self.update_status("준비 완료")
                
                # 어시스턴트 성공적으로 초기화됨
                self.after(0, lambda: self.add_system_message("어시스턴트가 성공적으로 초기화되었습니다."))
                
                # 메모리 상태 업데이트
                memory_status = "STM/LTM 활성화" if MEM0_AVAILABLE else "STM만 활성화 (LTM 없음)"
                self.after(0, lambda: self.memory_label.config(text=f"메모리: {memory_status}"))
                
                # STM 탭 초기 업데이트
                self.after(1000, self.update_stm_display)
                
                # 음성 인식 자동 시작 (RealtimeSTT 사용 가능한 경우)
                if REALTIME_STT_AVAILABLE:
                    self.after(1500, self.toggle_voice_recognition)
                
            except Exception as e:
                logging.error(f"어시스턴트 초기화 오류: {e}")
                self.is_assistant_ready = False
                
                # 오류 정보 표시
                error_detail = traceback.format_exc()
                self.after(0, lambda: messagebox.showerror("초기화 오류", 
                                                          f"어시스턴트를 초기화하지 못했습니다.\n\n{e}\n\n자세한 내용은 로그 탭을 확인하세요."))
                self.after(0, lambda: self.update_status("초기화 실패!"))
                
                # 로그 탭으로 전환
                self.after(0, lambda: self.tabs.select(3))  # 로그 탭 인덱스

        # 별도 스레드에서 초기화 실행
        self.assistant_thread = threading.Thread(target=initialize_worker)
        self.assistant_thread.daemon = True
        self.assistant_thread.start()

    def update_ui_timer(self):
        """주기적인 UI 업데이트를 위한 타이머"""
        # STM 업데이트 (어시스턴트가 준비되었을 때)
        if self.is_assistant_ready and hasattr(self.assistant, 'short_term_memory'):
            self.update_stm_display()
            
        # 상태 표시 업데이트
        if self.is_processing:
            # 처리 중인 경우 프로그레스 바 애니메이션
            if not self.progress_bar.winfo_ismapped():
                self.progress_bar.pack(fill=tk.X, padx=5, pady=5)
                self.progress_bar.start(10)
        else:
            # 처리 완료된 경우 프로그레스 바 숨김
            if self.progress_bar.winfo_ismapped():
                self.progress_bar.stop()
                self.progress_bar.pack_forget()
        
        # 타이머 재설정 (1초마다)
        self.after(1000, self.update_ui_timer)

    def update_stm_display(self):
        """단기 기억 표시 업데이트"""
        if not hasattr(self.assistant, 'short_term_memory'):
            return
            
        self.stm_text.config(state=tk.NORMAL)
        self.stm_text.delete(1.0, tk.END)
        
        if not self.assistant.short_term_memory:
            self.stm_text.insert(tk.END, "아직 저장된 단기 기억이 없습니다.")
        else:
            for i, memory in enumerate(self.assistant.short_term_memory, 1):
                self.stm_text.insert(tk.END, f"--- 기억 #{i} ---\n")
                self.stm_text.insert(tk.END, f"{memory}\n\n")
        
        self.stm_text.config(state=tk.DISABLED)

    def search_ltm(self, event=None):
        """장기 기억 검색"""
        if not self.is_assistant_ready or not MEM0_AVAILABLE:
            messagebox.showinfo("알림", "장기 기억 기능을 사용할 수 없습니다.")
            return
            
        query = self.ltm_search_entry.get().strip()
        if not query:
            messagebox.showinfo("검색", "검색어를 입력해주세요.")
            return
            
        try:
            self.update_status(f"장기 기억 검색 중: {query}")
            
            # 검색 실행
            memories = self.assistant.long_term_memory.search(
                query=query,
                user_id=config.MEMORY_USER_ID,
                limit=10
            )
            
            # 결과 표시
            self.ltm_text.config(state=tk.NORMAL)
            self.ltm_text.delete(1.0, tk.END)
            
            if not memories:
                self.ltm_text.insert(tk.END, "검색 결과가 없습니다.")
            else:
                self.ltm_text.insert(tk.END, f"검색어 '{query}'에 대한 결과:\n\n")
                
                for i, mem in enumerate(memories, 1):
                    memory_text = mem.get('memory', '내용 없음')
                    score = mem.get('score', 'N/A')
                    
                    self.ltm_text.insert(tk.END, f"--- 결과 #{i} (관련도: {score:.4f}) ---\n")
                    self.ltm_text.insert(tk.END, f"{memory_text}\n\n")
            
            self.ltm_text.config(state=tk.DISABLED)
            self.update_status("장기 기억 검색 완료")
            
        except Exception as e:
            logging.error(f"장기 기억 검색 오류: {e}")
            messagebox.showerror("검색 오류", f"장기 기억 검색 중 오류가 발생했습니다: {e}")
            self.update_status("장기 기억 검색 실패")

    def toggle_voice_recognition(self):
        """음성 인식 시작/중지"""
        if not REALTIME_STT_AVAILABLE:
            messagebox.showinfo("알림", "RealtimeSTT 모듈이 설치되지 않아 음성 입력을 사용할 수 없습니다.")
            return
            
        self.is_voice_active = not self.is_voice_active
        
        if self.is_voice_active:
            self.voice_button.config(text="음성 인식 중지")
            self.voice_status_label.config(text="🎤 음성 인식 활성화됨")
            self.add_system_message("음성 인식을 시작합니다. 말씀해주세요...")
            
            # 음성 인식 스레드 시작
            self.voice_recognition_thread = threading.Thread(target=self.continuous_voice_recognition, daemon=True)
            self.voice_recognition_thread.start()
        else:
            self.voice_button.config(text="음성 인식 시작")
            self.voice_status_label.config(text="🎤 음성 인식 준비됨")
            self.add_system_message("음성 인식을 중지합니다.")
            # 스레드는 daemon=True로 설정되어 있어 자동으로 종료됨

    def continuous_voice_recognition(self):
        """연속적인 음성 인식 수행"""
        if not self.is_assistant_ready:
            messagebox.showinfo("알림", "어시스턴트가 아직 준비되지 않았습니다.")
            self.is_voice_active = False
            self.voice_button.config(text="음성 인식 시작")
            return
            
        self.update_status("🎤 음성 인식 활성화됨")
        
        while self.is_voice_active:
            try:
                # 이미 처리 중이면 건너뛰기
                if self.is_processing:
                    time.sleep(1)
                    continue
                    
                self.is_processing = True
                
                # 음성 변환 시작
                self.update_status("🎤 음성 입력 대기 중...")
                transcribed_text = self.assistant.recorder.text()
                
                # 음성이 감지되지 않았거나 프로그램이 종료 중이면 계속
                if not transcribed_text or transcribed_text.strip() == "" or not self.is_voice_active:
                    self.is_processing = False
                    continue
                    
                # UI에 변환된 텍스트 표시
                self.add_user_message(transcribed_text)
                
                # LLM에 전송
                self.update_status("LLM에 전송 중...")
                self.process_llm_response(transcribed_text)
                
            except Exception as e:
                logging.error(f"음성 입력 처리 오류: {e}")
                self.add_system_message(f"음성 입력 처리 중 오류가 발생했습니다: {e}")
            finally:
                self.is_processing = False
                
        self.update_status("음성 인식 중지됨")

    def process_text_input(self, text):
        """텍스트 입력 처리 (별도 스레드에서 실행)"""
        def text_worker():
            self.is_processing = True
            try:
                self.update_status("LLM에 전송 중...")
                self.process_llm_response(text)
            except Exception as e:
                logging.error(f"텍스트 입력 처리 오류: {e}")
                self.add_system_message(f"오류가 발생했습니다: {e}")
            finally:
                self.is_processing = False
                
        threading.Thread(target=text_worker, daemon=True).start()

    def process_llm_response(self, input_text):
        """LLM 응답 처리 및 UI 업데이트"""
        try:
            # 임시 응답 텍스트 변수
            self.current_response = ""
            
            # 콜백 정의
            def response_callback(chunk):
                self.current_response += chunk
                # UI 스레드에서 텍스트 업데이트
                self.after(0, lambda: self.update_assistant_message(self.current_response))
            
            # 응답 메시지 준비
            self.add_assistant_message("")
            
            # 수정된 send_to_llm 로직을 여기서 직접 구현
            stm_context = "\n".join(self.assistant.short_term_memory) if hasattr(self.assistant, 'short_term_memory') and self.assistant.short_term_memory else "최근 대화 없음."
            
            # LTM 검색
            ltm_context = "관련된 장기 기억 없음."
            if hasattr(self.assistant, 'long_term_memory') and self.assistant.long_term_memory:
                try:
                    ltm_search_response = self.assistant.long_term_memory.search(
                        query=input_text,
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
                except Exception as e:
                    logging.error(f"LTM 검색 오류: {e}")
                    ltm_context = "장기 기억 검색 중 오류 발생."
            
            # 정체성 컨텍스트
            dynamic_identity_context = get_astra_siro_identity_context()
            
            # 최종 프롬프트 구성
            from system_prompts import MAIN_PROMPT_TEMPLATE
            prompt_with_context = MAIN_PROMPT_TEMPLATE.format(
                identity_context=dynamic_identity_context,
                short_term_memory=stm_context,
                long_term_memory=ltm_context,
                user_input=input_text
            )
            
            # Ollama API 페이로드
            payload = {
                "model": self.assistant.model,
                "prompt": prompt_with_context,
                "stream": True,
                "options": {
                    "num_gpu": config.NUM_GPU,
                    "temperature": self.assistant.temperature,
                }
            }
            
            headers = {'Content-Type': 'application/json'}
            full_response = ""
            
            # Ollama API 호출
            response = requests.post(
                self.assistant.ollama_url, 
                json=payload, 
                headers=headers, 
                stream=True,
                timeout=config.REQUEST_TIMEOUT * 6
            )
            response.raise_for_status()
            
            for line in response.iter_lines():
                if line:
                    decoded_line = line.decode('utf-8')
                    try:
                        json_chunk = json.loads(decoded_line)
                        response_part = json_chunk.get('response', '')
                        full_response += response_part
                        
                        # UI 업데이트
                        self.after(0, lambda: self.update_assistant_message(full_response))
                        
                        if json_chunk.get('done', False):
                            break
                    except json.JSONDecodeError:
                        logging.warning(f"응답 스트림 JSON 디코딩 오류 (무시): {decoded_line}")
                    except Exception as e:
                        logging.error(f"응답 스트림 처리 중 오류: {e}")
            
            # 대화 기억 저장
            if input_text and full_response.strip():
                interaction_to_save = f"사용자: {input_text}\n아스트라 시로: {full_response}"
                
                # STM 저장
                if hasattr(self.assistant, 'short_term_memory'):
                    self.assistant.short_term_memory.append(interaction_to_save)
                    logging.info("현재 대화를 STM에 추가했습니다.")
                    self.after(0, self.update_stm_display)
                
                # LTM 저장 (백그라운드)
                if hasattr(self.assistant, 'long_term_memory') and hasattr(self.assistant, 'save_to_ltm'):
                    ltm_save_thread = threading.Thread(
                        target=self.assistant.save_to_ltm,
                        args=(interaction_to_save,),
                        daemon=True
                    )
                    ltm_save_thread.start()
                    logging.info(f"LTM 저장을 위한 백그라운드 스레드 시작됨")
            
            self.update_status("준비 완료")
            
        except requests.exceptions.RequestException as e:
            logging.error(f"Ollama API 오류: {e}")
            self.add_system_message(f"Ollama API 오류: {e}")
        except Exception as e:
            logging.error(f"LLM 응답 처리 오류: {e}")
            self.add_system_message(f"오류가 발생했습니다: {e}")

    def add_user_message(self, message):
        """UI에 사용자 메시지 추가"""
        self.conversation_text.config(state=tk.NORMAL)
        if self.conversation_text.get("1.0", tk.END).strip():
            self.conversation_text.insert(tk.END, "\n\n")
        self.conversation_text.insert(tk.END, "사용자: ", "user")
        self.conversation_text.insert(tk.END, message)
        self.conversation_text.see(tk.END)
        self.conversation_text.config(state=tk.DISABLED)

    def add_assistant_message(self, message):
        """UI에 어시스턴트 메시지 추가"""
        self.conversation_text.config(state=tk.NORMAL)
        if self.conversation_text.get("1.0", tk.END).strip():
            self.conversation_text.insert(tk.END, "\n\n")
        self.conversation_text.insert(tk.END, "아스트라 시로: ", "assistant")
        self.conversation_text.insert(tk.END, message)
        self.conversation_text.see(tk.END)
        self.conversation_text.config(state=tk.DISABLED)

    def update_assistant_message(self, message):
        """마지막 어시스턴트 메시지 업데이트 (스트리밍용)"""
        # 마지막 메시지 위치 찾기
        self.conversation_text.config(state=tk.NORMAL)
        
        # 현재 텍스트 내용 가져오기
        current_text = self.conversation_text.get("1.0", tk.END)
        
        # 마지막 "아스트라 시로: " 위치 찾기
        last_prefix_pos = current_text.rfind("아스트라 시로: ")
        
        if last_prefix_pos != -1:
            # 마지막 메시지의 시작 위치 계산
            line_start = current_text.count("\n", 0, last_prefix_pos) + 1
            char_in_line = last_prefix_pos - current_text.rfind("\n", 0, last_prefix_pos) - 1
            start_pos = f"{line_start}.{char_in_line}"
            
            # 접두사 길이
            prefix_len = len("아스트라 시로: ")
            message_start = f"{line_start}.{char_in_line + prefix_len}"
            
            # 마지막 어시스턴트 메시지 삭제 및 새 메시지로 대체
            self.conversation_text.delete(message_start, tk.END)
            self.conversation_text.insert(message_start, message)
        else:
            # 마지막 메시지가 없는 경우, 새로 추가
            if self.conversation_text.get("1.0", tk.END).strip():
                self.conversation_text.insert(tk.END, "\n\n")
            self.conversation_text.insert(tk.END, "아스트라 시로: ", "assistant")
            self.conversation_text.insert(tk.END, message)
        
        # 스크롤 및 상태 복원
        self.conversation_text.see(tk.END)
        self.conversation_text.config(state=tk.DISABLED)

    def add_system_message(self, message):
        """UI에 시스템 메시지 추가"""
        self.conversation_text.config(state=tk.NORMAL)
        if self.conversation_text.get("1.0", tk.END).strip():
            self.conversation_text.insert(tk.END, "\n\n")
        self.conversation_text.insert(tk.END, f"[시스템: {message}]", "system")
        self.conversation_text.see(tk.END)
        self.conversation_text.config(state=tk.DISABLED)

    def update_status(self, message):
        """상태 표시줄 업데이트"""
        self.status_label.config(text=message)

    def update_temp_label(self, event):
        """온도 슬라이더 값 변경 시 라벨 업데이트"""
        temp_value = self.temperature_var.get()
        self.temp_label.config(text=f"{temp_value:.2f}")

    def save_conversation(self):
        """대화 내역 저장"""
        try:
            filename = filedialog.asksaveasfilename(
                defaultextension=".txt",
                filetypes=[("텍스트 파일", "*.txt"), ("모든 파일", "*.*")],
                title="대화 내역 저장"
            )
            
            if not filename:
                return  # 사용자가 취소함
                
            with open(filename, "w", encoding="utf-8") as file:
                file.write(self.conversation_text.get("1.0", tk.END))
                
            messagebox.showinfo("저장 완료", f"대화 내역이 {filename}에 저장되었습니다.")
            
        except Exception as e:
            logging.error(f"대화 내역 저장 오류: {e}")
            messagebox.showerror("저장 오류", f"대화 내역 저장 중 오류가 발생했습니다: {e}")

    def clear_conversation(self):
        """대화 내역 지우기"""
        if messagebox.askyesno("확인", "대화 내역을 모두 지우시겠습니까?"):
            self.conversation_text.config(state=tk.NORMAL)
            self.conversation_text.delete("1.0", tk.END)
            self.conversation_text.config(state=tk.DISABLED)
            self.add_system_message("대화 내역이 지워졌습니다.")

    def clear_logs(self):
        """로그 지우기"""
        self.logs_text.config(state=tk.NORMAL)
        self.logs_text.delete("1.0", tk.END)
        self.logs_text.config(state=tk.DISABLED)

    def change_log_level(self, event=None):
        """로그 레벨 변경"""
        new_level = self.log_level_var.get()
        logging.getLogger().setLevel(getattr(logging, new_level))
        logging.info(f"로그 레벨이 {new_level}로 변경되었습니다.")

    def apply_model_change(self):
        """모델 변경 적용"""
        new_model = self.model_var.get().strip()
        if not new_model:
            messagebox.showinfo("알림", "모델 이름을 입력해주세요.")
            return
            
        if not self.is_assistant_ready:
            messagebox.showinfo("알림", "어시스턴트가 아직 초기화되지 않았습니다.")
            return
            
        # 모델 변경
        self.assistant.model = new_model
        
        # UI 업데이트
        self.model_label.config(text=f"모델: {new_model}")
        self.add_system_message(f"모델이 {new_model}로 변경되었습니다.")
        
        logging.info(f"LLM 모델이 {new_model}로 변경되었습니다.")

    def test_ollama_connection(self):
        """Ollama 서버 연결 테스트"""
        host = self.ollama_host_var.get().strip()
        try:
            port = int(self.ollama_port_var.get())
        except ValueError:
            messagebox.showerror("입력 오류", "포트는 숫자로 입력해주세요.")
            return
            
        test_url = f"http://{host}:{port}/api/version"
        
        try:
            self.update_status(f"Ollama 서버 연결 테스트 중...")
            response = requests.get(test_url, timeout=config.REQUEST_TIMEOUT)
            response.raise_for_status()
            
            version_info = response.json()
            messagebox.showinfo("연결 성공", f"Ollama 서버에 연결되었습니다.\n\n버전: {version_info.get('version', '알 수 없음')}")
            self.update_status("Ollama 서버 연결 확인됨")
            
        except requests.exceptions.RequestException as e:
            logging.error(f"Ollama 서버 연결 테스트 실패: {e}")
            messagebox.showerror("연결 실패", f"Ollama 서버에 연결할 수 없습니다.\n\n오류: {e}")
            self.update_status("Ollama 서버 연결 실패")

    def change_model(self):
        """모델 변경 다이얼로그"""
        new_model = tk.simpledialog.askstring(
            "모델 변경",
            "새 모델 이름을 입력하세요:",
            initialvalue=self.model_var.get()
        )
        
        if new_model:
            self.model_var.set(new_model)
            self.apply_model_change()

    def change_temperature(self):
        """온도 조정 다이얼로그"""
        try:
            new_temp = float(tk.simpledialog.askstring(
                "온도 조정",
                "새 온도 값을 입력하세요 (0.0 ~ 1.0):",
                initialvalue=f"{self.temperature_var.get():.2f}"
            ))
            
            if 0.0 <= new_temp <= 1.0:
                self.temperature_var.set(new_temp)
                self.temp_label.config(text=f"{new_temp:.2f}")
                
                if self.is_assistant_ready:
                    self.assistant.temperature = new_temp
                    self.add_system_message(f"AI 온도가 {new_temp:.2f}로 변경되었습니다.")
                    logging.info(f"AI 온도가 {new_temp:.2f}로 변경되었습니다.")
            else:
                messagebox.showwarning("입력 오류", "온도는 0.0에서 1.0 사이의 값이어야 합니다.")
        except (ValueError, TypeError):
            messagebox.showwarning("입력 오류", "유효한 숫자를 입력해주세요.")

    def manage_memory(self):
        """메모리 관리 다이얼로그"""
        if not MEM0_AVAILABLE:
            messagebox.showinfo("알림", "장기 기억 기능을 사용할 수 없습니다. (mem0 없음)")
            return
            
        memory_dialog = tk.Toplevel(self)
        memory_dialog.title("메모리 관리")
        memory_dialog.geometry("600x400")
        memory_dialog.transient(self)
        memory_dialog.grab_set()
        
        ttk.Label(memory_dialog, text="메모리 관리", font=("맑은 고딕", 12, "bold")).pack(pady=10)
        
        # STM 컨트롤
        stm_frame = ttk.LabelFrame(memory_dialog, text="단기 기억 (STM)")
        stm_frame.pack(fill=tk.X, padx=10, pady=5)
        
        ttk.Button(stm_frame, text="단기 기억 초기화", command=self.clear_stm).pack(padx=10, pady=5)
        
        # LTM 컨트롤
        ltm_frame = ttk.LabelFrame(memory_dialog, text="장기 기억 (LTM)")
        ltm_frame.pack(fill=tk.X, padx=10, pady=5)
        
        ttk.Button(ltm_frame, text="모든 장기 기억 조회", 
                  command=lambda: self.view_all_ltm(memory_dialog)).pack(padx=10, pady=5)
        
        ttk.Button(ltm_frame, text="장기 기억 초기화 (위험)", 
                  command=lambda: self.confirm_clear_ltm(memory_dialog)).pack(padx=10, pady=5)
        
        ttk.Button(memory_dialog, text="닫기", command=memory_dialog.destroy).pack(pady=10)

    def view_all_ltm(self, parent_dialog):
        """모든 장기 기억 조회"""
        if not self.is_assistant_ready or not hasattr(self.assistant, 'long_term_memory'):
            messagebox.showinfo("알림", "장기 기억 기능을 사용할 수 없습니다.")
            return
            
        try:
            # 빈 검색으로 모든 메모리 가져오기
            memories = self.assistant.long_term_memory.search(
                query="",
                user_id=config.MEMORY_USER_ID,
                limit=100
            )
            
            view_dialog = tk.Toplevel(parent_dialog)
            view_dialog.title("모든 장기 기억")
            view_dialog.geometry("700x500")
            view_dialog.transient(parent_dialog)
            
            ttk.Label(view_dialog, text="모든 장기 기억 목록", font=("맑은 고딕", 11, "bold")).pack(pady=5)
            
            # 메모리 텍스트 영역
            memory_text = scrolledtext.ScrolledText(
                view_dialog, wrap=tk.WORD, font=("맑은 고딕", 9)
            )
            memory_text.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
            
            if not memories:
                memory_text.insert(tk.END, "저장된 장기 기억이 없습니다.")
            else:
                memory_text.insert(tk.END, f"총 {len(memories)}개의 장기 기억이 있습니다.\n\n")
                
                for i, mem in enumerate(memories, 1):
                    memory_text = mem.get('memory', '내용 없음')
                    memory_text.insert(tk.END, f"--- 기억 #{i} ---\n")
                    memory_text.insert(tk.END, f"{memory_text}\n\n")
            
            ttk.Button(view_dialog, text="닫기", command=view_dialog.destroy).pack(pady=10)
            
        except Exception as e:
            logging.error(f"장기 기억 조회 오류: {e}")
            messagebox.showerror("오류", f"장기 기억 조회 중 오류가 발생했습니다: {e}")

    def confirm_clear_ltm(self, parent_dialog):
        """장기 기억 초기화 확인"""
        if messagebox.askyesno("위험", "정말로 모든 장기 기억을 초기화하시겠습니까?\n이 작업은 되돌릴 수 없습니다!", parent=parent_dialog):
            self.clear_ltm(parent_dialog)

    def clear_stm(self):
        """단기 기억 초기화"""
        if not self.is_assistant_ready:
            messagebox.showinfo("알림", "어시스턴트가 아직 초기화되지 않았습니다.")
            return
            
        try:
            self.assistant.short_term_memory.clear()
            self.update_stm_display()
            self.add_system_message("단기 기억이 초기화되었습니다.")
            logging.info("단기 기억이 초기화되었습니다.")
        except Exception as e:
            logging.error(f"단기 기억 초기화 오류: {e}")
            messagebox.showerror("오류", f"단기 기억 초기화 중 오류가 발생했습니다: {e}")

    def clear_ltm(self, parent_dialog=None):
        """장기 기억 초기화"""
        if not self.is_assistant_ready or not hasattr(self.assistant, 'long_term_memory'):
            messagebox.showinfo("알림", "장기 기억 기능을 사용할 수 없습니다.", parent=parent_dialog)
            return
            
        try:
            # ChromaDB 컬렉션 삭제 (LTM 초기화)
            self.assistant.long_term_memory.vector_store.delete_collection()
            self.add_system_message("장기 기억이 초기화되었습니다.")
            logging.info("장기 기억이 초기화되었습니다.")
            
            # LTM 재초기화
            self.assistant.long_term_memory = self.assistant.setup_mem0_for_ltm()
            
            if parent_dialog:
                messagebox.showinfo("완료", "장기 기억이 성공적으로 초기화되었습니다.", parent=parent_dialog)
                
        except Exception as e:
            logging.error(f"장기 기억 초기화 오류: {e}")
            messagebox.showerror("오류", f"장기 기억 초기화 중 오류가 발생했습니다: {e}", parent=parent_dialog)

    def apply_settings(self):
        """모든 설정 적용"""
        if not self.is_assistant_ready:
            messagebox.showinfo("알림", "어시스턴트가 아직 초기화되지 않았습니다.")
            return
            
        # 모델 변경
        new_model = self.model_var.get().strip()
        if new_model and new_model != self.assistant.model:
            self.assistant.model = new_model
            self.model_label.config(text=f"모델: {new_model}")
            logging.info(f"LLM 모델이 {new_model}로 변경되었습니다.")
        
        # 온도 변경
        new_temp = self.temperature_var.get()
        if new_temp != self.assistant.temperature:
            self.assistant.temperature = new_temp
            logging.info(f"AI 온도가 {new_temp:.2f}로 변경되었습니다.")
        
        # STT 모델 변경 (RealtimeSTT가 사용 가능한 경우)
        if REALTIME_STT_AVAILABLE:
            new_stt_model = self.stt_model_var.get()
            if new_stt_model != self.assistant.stt_model:
                try:
                    # STT 레코더 재설정
                    self.assistant.stt_model = new_stt_model
                    self.assistant.setup_stt_recorder()
                    logging.info(f"STT 모델이 {new_stt_model}로 변경되었습니다.")
                except Exception as e:
                    logging.error(f"STT 모델 변경 오류: {e}")
                    messagebox.showerror("오류", f"STT 모델 변경 중 오류가 발생했습니다: {e}")
        
        # Ollama 서버 변경
        new_host = self.ollama_host_var.get().strip()
        try:
            new_port = int(self.ollama_port_var.get())
        except ValueError:
            messagebox.showerror("입력 오류", "포트는 숫자로 입력해주세요.")
            return
            
        if new_host != config.OLLAMA_HOST or new_port != config.OLLAMA_PORT:
            # 여기서는 config.py를 직접 수정하지 않고, 다음 실행 시 적용되도록 안내
            messagebox.showinfo("서버 설정", 
                               "Ollama 서버 설정은 config.py 파일에서 직접 변경해야 합니다. 변경 후 프로그램을 재시작하세요.")
        
        # STM 크기 변경
        new_stm_size = self.stm_size_var.get()
        if hasattr(self.assistant, 'short_term_memory') and new_stm_size != self.assistant.short_term_memory.maxlen:
            # 새 deque 생성하고 기존 항목 복사
            import collections
            new_stm = collections.deque(self.assistant.short_term_memory, maxlen=new_stm_size)
            self.assistant.short_term_memory = new_stm
            logging.info(f"STM 크기가 {new_stm_size}로 변경되었습니다.")
        
        self.add_system_message("설정이 적용되었습니다.")

    def open_config_file(self):
        """config.py 파일 열기"""
        try:
            if os.path.isfile("config.py"):
                if sys.platform == "win32":
                    os.startfile("config.py")
                elif sys.platform == "darwin":  # macOS
                    os.system("open config.py")
                else:  # Linux
                    os.system("xdg-open config.py")
            else:
                messagebox.showinfo("알림", "config.py 파일을 찾을 수 없습니다.")
        except Exception as e:
            logging.error(f"설정 파일 열기 오류: {e}")
            messagebox.showerror("오류", f"설정 파일을 열 수 없습니다: {e}")

    def show_about(self):
        """프로그램 정보 표시"""
        about_msg = """아스트라 시로 AI 어시스턴트 통합 UI

버전: 1.0
© 2025 아스트라 시로 프로젝트

이 프로그램은 Ollama API를 사용하여 LLM과 통신하며,
음성 또는 텍스트 입력을 지원합니다.

아스트라 시로 페르소나를 연기하는 AI 동반자입니다."""

        messagebox.showinfo("프로그램 정보", about_msg)

    def show_system_status(self):
        """시스템 상태 정보 표시"""
        if not self.is_assistant_ready:
            messagebox.showinfo("알림", "어시스턴트가 아직 초기화되지 않았습니다.")
            return
            
        status_msg = f"""시스템 상태

LLM 모델: {self.assistant.model}
AI 온도: {self.assistant.temperature:.2f}
Ollama 서버: {config.OLLAMA_HOST}:{config.OLLAMA_PORT}

음성 인식: {"사용 가능" if REALTIME_STT_AVAILABLE else "사용 불가"}
STT 모델: {self.assistant.stt_model if REALTIME_STT_AVAILABLE else "N/A"}
STT 디바이스: {self.assistant.device if REALTIME_STT_AVAILABLE else "N/A"}

단기 기억: {"활성화" if hasattr(self.assistant, 'short_term_memory') else "비활성화"}
단기 기억 크기: {len(self.assistant.short_term_memory) if hasattr(self.assistant, 'short_term_memory') else 0} / {self.assistant.short_term_memory.maxlen if hasattr(self.assistant, 'short_term_memory') else 0}

장기 기억: {"활성화" if MEM0_AVAILABLE and hasattr(self.assistant, 'long_term_memory') else "비활성화"}
Vector Store: {config.VECTOR_STORE_PROVIDER if MEM0_AVAILABLE else "N/A"}
임베딩 모델: {config.MEM0_EMBEDDING_MODEL if MEM0_AVAILABLE else "N/A"}

로그 레벨: {logging.getLevelName(logging.getLogger().level)}
로그 경로: {config.LOG_DIR}
"""
        
        messagebox.showinfo("시스템 상태", status_msg)

    def on_closing(self):
        """프로그램 종료 처리"""
        if messagebox.askokcancel("종료", "프로그램을 종료하시겠습니까?"):
            # 음성 인식 중지
            self.is_voice_active = False
            
            # 어시스턴트 정리
            if self.is_assistant_ready and hasattr(self.assistant, 'recorder') and hasattr(self.assistant.recorder, 'shutdown'):
                try:
                    self.assistant.recorder.shutdown()
                    logging.info("STT 레코더가 정상적으로 종료되었습니다.")
                except Exception as e:
                    logging.error(f"STT 레코더 종료 오류: {e}")
            
            logging.info("프로그램이 종료됩니다.")
            self.destroy()


if __name__ == "__main__":
    try:
        app = AstraUI()
        app.mainloop()
    except Exception as e:
        # 시작 실패 시 로깅 없이 직접 에러 메시지 표시
        print(f"프로그램 시작 오류: {e}")
        traceback.print_exc()
        messagebox.showerror("심각한 오류", f"프로그램을 시작할 수 없습니다: {e}")