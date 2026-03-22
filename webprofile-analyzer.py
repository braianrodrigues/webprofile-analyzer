# =====================================================
# WebProfile Analyzer - 
# Autor: Braian Rodrigues
# Arquivo único: captura de fotos, ajuste de recorte
# e salvamento de sessão do WhatsApp Web.
# =====================================================

import os
import sys
import json
import time
import threading
import io

import tkinter as tk
from tkinter import filedialog, messagebox
from tkinter.scrolledtext import ScrolledText

from PIL import Image, ImageTk

# Selenium / WhatsApp
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.chrome.options import Options
import chromedriver_autoinstaller
from webdriver_manager.chrome import ChromeDriverManager
from selenium.common.exceptions import ElementClickInterceptedException, TimeoutException


# =====================================================
# CONFIGURAÇÕES GERAIS
# =====================================================

APP_TITLE = "WebProfile Analyzer"
DATE_FORMAT = "%d/%m/%Y %H:%M:%S"

# =========================
# UI WAIT TUNING
# =========================
DELAY_SHORT_SEC = 0.4
DELAY_MED_SEC = 1.0
DELAY_LONG_SEC = 2.0

def ui_sleep(seconds: float):
    time.sleep(seconds)

def ui_short():
    ui_sleep(DELAY_SHORT_SEC)

def ui_med():
    ui_sleep(DELAY_MED_SEC)

def ui_long():
    ui_sleep(DELAY_LONG_SEC)


# =========================
# Selenium click helpers
# =========================
def safe_js_click(driver, element) -> bool:
    try:
        driver.execute_script("arguments[0].click();", element)
        return True
    except Exception:
        return False

def safe_click(driver, element, scroll: bool = True) -> bool:
    try:
        if scroll:
            try:
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", element)
                ui_short()
            except Exception:
                pass
        element.click()
        return True
    except ElementClickInterceptedException:
        return safe_js_click(driver, element)
    except Exception:
        return False



def resource_path(relative_path: str) -> str:
    """
    Retorna o caminho correto para arquivos (ico, etc)
    tanto no .py quanto no .exe gerado pelo PyInstaller.
    """
    if hasattr(sys, "_MEIPASS"):
        base_path = sys._MEIPASS  # type: ignore[attr-defined]
    else:
        base_path = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_path, relative_path)


# BASE_DIR: pasta onde está o .py ou o .exe
if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.realpath(__file__))

PROFILE_PATH = os.path.join(BASE_DIR, "selenium_profile")
ICON_PATH = resource_path("webprofile-analyzer.ico")

# arquivo global com as coordenadas de recorte (persistente ao lado do exe)
GLOBAL_CROP_CONFIG = os.path.join(BASE_DIR, "crop_coords_config.json")

root = None
log_text = None

# Flags de controle da rotina de captura
pause_event = threading.Event()
stop_event = threading.Event()
processing_event = threading.Event()  # indica se há captura em andamento

pause_button = None
stop_button = None


# =====================================================
# UTILIDADES DE LOG NA GUI
# =====================================================

def gui_log(msg: str):
    """Escreve mensagem na área de log da GUI (thread-safe)."""
    global root, log_text
    if root is None or log_text is None:
        print(msg)
        return

    def _append():
        log_text.config(state=tk.NORMAL)
        log_text.insert(tk.END, msg + "\n")
        log_text.see(tk.END)
        log_text.config(state=tk.DISABLED)

    root.after(0, _append)


class TextRedirector:
    """
    Redireciona prints para o log da GUI.
    """
    def __init__(self, write_fn):
        self.write_fn = write_fn

    def write(self, s):
        s = s.rstrip()
        if s:
            self.write_fn(s)

    def flush(self):
        pass

# =====================================================
# INSTALAÇÃO / CRIAÇÃO DO CHROMEDRIVER
# =====================================================

def install_chromedriver():
    """Tenta instalar via chromedriver-autoinstaller, cai para webdriver-manager."""
    try:
        chromedriver_autoinstaller.install()
        service = ChromeService()
        gui_log("ChromeDriver instalado com sucesso (chromedriver-autoinstaller).")
        return service
    except Exception as e:
        gui_log(f"Falha no chromedriver-autoinstaller: {e}")
        service = ChromeService(executable_path=ChromeDriverManager().install())
        gui_log("ChromeDriver instalado com sucesso (webdriver-manager).")
        return service


def create_driver_for_whatsapp(headless: bool = False):
    """Cria driver com PROFILE_PATH para manter sessão do WhatsApp."""
    chrome_options = Options()
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1280,1024")
    chrome_options.add_argument(f"user-data-dir={PROFILE_PATH}")

    if headless:
        chrome_options.add_argument("--headless=new")

    service = install_chromedriver()
    driver = webdriver.Chrome(service=service, options=chrome_options)
    return driver


# =====================================================
# FUNÇÕES DE RECORTE GLOBAL
# =====================================================

def load_global_crop_coords():
    """
    Lê o arquivo global de recorte (crop_coords_config.json) salvo ao lado do exe.
    Retorna tupla (left, top, right, bottom) ou None se não existir.
    """
    try:
        if os.path.exists(GLOBAL_CROP_CONFIG):
            with open(GLOBAL_CROP_CONFIG, "r", encoding="utf-8") as f:
                data = json.load(f)
            coords = data.get("crop_coords")
            if isinstance(coords, (list, tuple)) and len(coords) == 4:
                return tuple(coords)
    except Exception as e:
        gui_log(f"Erro ao ler crop_coords_config.json: {e}")
    return None


def get_crop_coords():
    """
    Retorna as coordenadas de recorte a partir do arquivo global
    ou um padrão inicial, caso ainda não tenha sido configurado.
    """
    coords = load_global_crop_coords()
    if coords:
        return coords

    # coordenadas padrão aproximadas para 1280x1024 – você ajusta via "Ajustar recorte"
    return (585.0, 5.0, 1266.0, 853.0)


# =====================================================
# PARTE 1: CAPTURA DE FOTOS
# =====================================================

def crop_from_image_obj(img: Image.Image, contact_number: str, download_dir: str):
    """
    Recebe um objeto PIL.Image (tela cheia),
    aplica o recorte global e salva NUM_dados_contato.png.
    """
    left, top, right, bottom = get_crop_coords()
    cropped_img = img.crop((left, top, right, bottom))

    dest_path = os.path.join(download_dir, f"{contact_number}_dados_contato.png")
    cropped_img.save(dest_path)


def read_numbers_from_json(json_path):
    """
    Lê números de symmetric_contacts, asymmetric_contacts e groups,
    ignorando os que já estão em 'verificados'.
    """
    with open(json_path, 'r', encoding='utf-8') as file:
        data = json.load(file)

    numbers = set()
    numbers.update(data.get('symmetric_contacts', []))
    numbers.update(data.get('asymmetric_contacts', []))

    for group_id, group_numbers in data.get('groups', {}).items():
        numbers.update(group_numbers)

    verified_numbers = set(data.get('verificados', []))
    numbers_to_check = numbers - verified_numbers

    return numbers_to_check, verified_numbers


def save_verified_progress(json_path, verified_numbers):
    """
    Salva a lista atual de verificados no JSON.
    """
    try:
        with open(json_path, 'r+', encoding='utf-8') as file:
            data = json.load(file)
            data['verificados'] = list(verified_numbers)
            file.seek(0)
            json.dump(data, file, ensure_ascii=False, indent=4)
            file.truncate()
    except Exception as e:
        gui_log(f"Erro ao salvar progresso em {json_path}: {e}")


def wait_if_paused():
    """
    Bloqueia a execução enquanto o pause_event estiver ativo.
    Permite que o stop_event interrompa mesmo em pausa.
    """
    while pause_event.is_set() and not stop_event.is_set():
        time.sleep(0.3)



def has_profile_without_photo(driver) -> bool:
    """
    Robust detection for "Perfil sem foto" (and small variations).
    """
    texts = ["Perfil sem foto", "Sem foto", "sem foto", "No profile photo", "no profile photo"]
    for t in texts:
        if driver.find_elements(By.XPATH, f"//*[contains(normalize-space(), '{t}')]"):
            return True
    return False


def find_show_photo_button(driver):
    """
    Returns the 'Mostrar foto' button if present, else None.
    """
    try:
        return WebDriverWait(driver, 3).until(
            EC.presence_of_element_located((By.XPATH, "//div[@role='button' and @aria-label='Mostrar foto']"))
        )
    except TimeoutException:
        return None


def open_profile_panel(driver):
    """
    Clicks 'Dados do perfil' and waits for the contact drawer to open.
    """
    btn = WebDriverWait(driver, 20).until(
        EC.presence_of_element_located((By.XPATH, "//div[@title='Dados do perfil' and @role='button']"))
    )
    if not safe_click(driver, btn, scroll=True):
        safe_js_click(driver, btn)
    ui_long()


def save_expanded_photo(driver, contact_number: str, download_dir: str):
    """
    Opens the photo viewer and saves a full screenshot.
    Raises TimeoutException if viewer cannot be opened.
    """
    btn = find_show_photo_button(driver)
    if btn is None:
        raise TimeoutException("Show photo button not found")

    if not safe_click(driver, btn, scroll=True):
        safe_js_click(driver, btn)

    ui_long()

    # Wait viewer close button (WhatsApp variations)
    close_btn = WebDriverWait(driver, 10).until(
        EC.presence_of_element_located((By.XPATH, "//span[@data-icon='x-viewer' or @data-icon='x-viewer-outline']"))
    )
    ui_med()

    expanded_screenshot_path = os.path.join(download_dir, f"{contact_number}_foto_expandida.png")
    driver.save_screenshot(expanded_screenshot_path)

    safe_click(driver, close_btn, scroll=False)
    ui_med()


def save_profile_picture_and_info(contact_number, driver, download_dir, save_fullscreen_sample: bool):
    """
    Abre nova conversa, entra no perfil e faz:
      - screenshot em memória da TELA CHEIA do WhatsApp (painel de dados aberto)
      - se save_fullscreen_sample=True, salva uma vez amostra_tela_cheia.png
      - recorta o card e salva NUM_dados_contato.png
      - se existir, salva NUM_foto_expandida.png
    """
    status_msg = ""
    try:
        driver.execute_script("document.body.focus();")
        ui_short()

        # Botão nova conversa
        new_chat_button = WebDriverWait(driver, 20).until(
            EC.element_to_be_clickable((By.XPATH, "//span[@data-icon='new-chat-outline']"))
        )
        safe_click(driver, new_chat_button, scroll=False)
        ui_med()

        # Campo de busca (PT/EN)
        search_box = WebDriverWait(driver, 20).until(
            EC.presence_of_element_located(
                (By.XPATH,
                 "//div[@contenteditable='true' and (@aria-label='Pesquisar nome ou número' or @aria-label='Search name or number')]")
            )
        )

        safe_click(driver, search_box, scroll=False)
        try:
            driver.execute_script("arguments[0].focus();", search_box)
        except Exception:
            pass

        # Limpar
        search_box.send_keys(Keys.CONTROL, "a")
        search_box.send_keys(Keys.DELETE)
        ui_short()

        # Digitar número e confirmar
        search_box.send_keys(contact_number)
        ui_med()
        search_box.send_keys(Keys.ENTER)

        # aqui é o ponto que mais “quebra” layout -> 2s
        ui_long()

        # Nenhum resultado?
        if driver.find_elements(By.XPATH, "//span[contains(text(), 'Nenhum resultado encontrado')]"):
            status_msg = "Nenhum resultado encontrado."
            try:
                search_box.send_keys(Keys.ESCAPE)
            except Exception:
                pass
            ui_short()
            print(f"Verificando {contact_number} - {status_msg}")
            return True, False

        # Abre painel do contato (Dados do contato)
        open_profile_panel(driver)

        # Screenshot em memória (tela cheia)
        png_bytes = driver.get_screenshot_as_png()
        img = Image.open(io.BytesIO(png_bytes))

        sample_saved_now = False
        if save_fullscreen_sample:
            sample_path = os.path.join(download_dir, "amostra_tela_cheia.png")
            img.save(sample_path)
            sample_saved_now = True

        # Recorta card e salva
        crop_from_image_obj(img, contact_number, download_dir)
        ui_short()

        # Se não tem foto, não tenta viewer
        if has_profile_without_photo(driver) or find_show_photo_button(driver) is None:
            status_msg = "Não possui foto."
            print(f"Verificando {contact_number} - {status_msg}")
            return True, sample_saved_now

        # Tenta salvar expandida
        try:
            save_expanded_photo(driver, contact_number, download_dir)
            status_msg = "Foto salva."
            print(f"Verificando {contact_number} - {status_msg}")
            return True, sample_saved_now
        except TimeoutException:
            status_msg = "Não possui foto."
            print(f"Verificando {contact_number} - {status_msg}")
            return True, sample_saved_now
        except Exception:
            status_msg = "Não possui foto."
            print(f"Verificando {contact_number} - {status_msg}")
            return True, sample_saved_now

    except Exception as e:
        status_msg = f"ERRO: {str(e)}"
        print(f"Verificando {contact_number} - {status_msg}")
        return False, False


def execute_capture_routine(base_dir: str):
    """
    Rotina principal de captura:
    - base_dir: pasta escolhida no botão da GUI.
    - Se existir data.json diretamente nela, processa.
    - Também procura subpastas com data.json.

    Agora:
    - Salva apenas UMA "amostra_tela_cheia.png" por pasta (primeiro contato bem-sucedido).
    - Para todos os contatos, salva apenas NUM_dados_contato.png (recortado).
    """
    global pause_event, stop_event, processing_event

    base_dir = os.path.abspath(base_dir)
    gui_log(f"Iniciando processamento em: {base_dir}")

    stop_event.clear()
    pause_event.clear()
    processing_event.set()

    candidate_folders = []

    json_here = os.path.join(base_dir, "data.json")
    if os.path.exists(json_here):
        candidate_folders.append(base_dir)

    for folder_name in os.listdir(base_dir):
        folder_path = os.path.join(base_dir, folder_name)
        if os.path.isdir(folder_path) and folder_path != base_dir:
            json_path = os.path.join(folder_path, "data.json")
            if os.path.exists(json_path):
                candidate_folders.append(folder_path)

    if not candidate_folders:
        gui_log("Nenhuma pasta com data.json encontrada nessa pasta.")
        processing_event.clear()
        return

    old_stdout = sys.stdout
    sys.stdout = TextRedirector(gui_log)

    try:
        for folder_path in candidate_folders:
            if stop_event.is_set():
                gui_log("Parada solicitada antes de iniciar a próxima pasta. Encerrando rotina.")
                break

            folder_name = os.path.basename(folder_path)
            json_path = os.path.join(folder_path, "data.json")
            photos_dir = os.path.join(folder_path, "Fotos Números")
            if not os.path.exists(photos_dir):
                os.makedirs(photos_dir)

            try:
                contact_numbers, verified_numbers = read_numbers_from_json(json_path)
                if not contact_numbers:
                    gui_log(f"[{folder_name}] Nenhum número novo para verificar.")
                    continue

                driver = create_driver_for_whatsapp(headless=True)
                driver.get("https://web.whatsapp.com")
                gui_log(f"[{folder_name}] Aguardando carregamento do WhatsApp (sessão salva).")
                time.sleep(30)

                gui_log(f"[{folder_name}] Iniciando o processamento de {len(contact_numbers)} números...")
                
                sample_saved = False  # controla se já salvamos amostra_tela_cheia nessa pasta

                try:
                    for number in contact_numbers:
                        if stop_event.is_set():
                            gui_log("Parada solicitada. Salvando progresso e encerrando pasta atual.")
                            break

                        wait_if_paused()
                        if stop_event.is_set():
                            gui_log("Parada solicitada durante pausa. Encerrando pasta atual.")
                            break

                        save_sample_for_this = not sample_saved

                        ok, sample_saved_now = save_profile_picture_and_info(
                            number,
                            driver,
                            photos_dir,
                            save_fullscreen_sample=save_sample_for_this
                        )

                        if sample_saved_now:
                            sample_saved = True

                        if ok:
                            verified_numbers.add(number)
                        else:
                            gui_log(f"Erro ao processar {number}. Pulando para o próximo.")
                            continue

                        save_verified_progress(json_path, verified_numbers)                       

                        time.sleep(1)

                finally:
                    try:
                        driver.quit()
                    except Exception:
                        pass

                if stop_event.is_set():
                    gui_log(f"[{folder_name}] Processamento interrompido pelo usuário. Progresso salvo.")
                    break                

                gui_log(f"[{folder_name}] Processamento concluído. JSON atualizado.")

            except Exception as e:
                gui_log(f"Erro ao processar a pasta {folder_name}: {str(e)}")
                processing_event.clear()
                pause_event.clear()
                stop_event.clear()                
                return

    finally:
        sys.stdout = old_stdout
        processing_event.clear()
        pause_event.clear()
        stop_event.clear()
        gui_log("Rotina de fotos finalizada.")


# =====================================================
# PARTE 2: SALVAR SESSÃO DO WHATSAPP
# =====================================================

def save_whatsapp_session():
    gui_log("Abrindo WhatsApp Web para salvar a sessão...")

    driver = None
    try:
        driver = create_driver_for_whatsapp(headless=False)
        driver.get("https://web.whatsapp.com")
        gui_log("Escaneie o QR Code com o celular. Aguardando 30 segundos...")
        time.sleep(30)
        gui_log("Sessão salva com sucesso. Agora você pode fechar o navegador.")
        messagebox.showinfo("Sucesso", "Sessão do WhatsApp salva com sucesso!")
    except Exception as e:
        gui_log(f"Erro ao tentar salvar a sessão: {e}")
        messagebox.showerror("Erro", f"Erro ao salvar sessão do WhatsApp:\n{e}")
    finally:
        if driver:
            driver.quit()


# =====================================================
# PARTE 3: AJUSTE DE RECORTE (ImageCropper)
# =====================================================

class ImageCropper(tk.Toplevel):
    def __init__(self, master, image_path):
        super().__init__(master)
        self.title("Ajuste de recorte da imagem")

        self.img = Image.open(image_path)
        self.tk_img = ImageTk.PhotoImage(self.img)

        self.canvas = tk.Canvas(self, bg='gray')
        scroll_x = tk.Scrollbar(self, orient="horizontal", command=self.canvas.xview)
        scroll_y = tk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(xscrollcommand=scroll_x.set, yscrollcommand=scroll_y.set)

        self.canvas.create_image(0, 0, anchor="nw", image=self.tk_img)
        self.canvas.config(scrollregion=self.canvas.bbox(tk.ALL))

        self.canvas.grid(row=0, column=0, sticky="nsew")
        scroll_x.grid(row=1, column=0, sticky="ew")
        scroll_y.grid(row=0, column=1, sticky="ns")

        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)

        button_frame = tk.Frame(self)
        button_frame.grid(row=2, column=0, columnspan=2, pady=10)

        save_button = tk.Button(button_frame, text="Salvar recorte",
                                command=self.save_crop)
        save_button.pack(side=tk.LEFT, padx=10)

        cancel_button = tk.Button(button_frame, text="Cancelar", command=self.destroy)
        cancel_button.pack(side=tk.RIGHT, padx=10)

        self.rect = None
        self.start_x = None
        self.start_y = None
        self.crop_coords = None

        self.canvas.bind("<ButtonPress-1>", self.on_button_press)
        self.canvas.bind("<B1-Motion>", self.on_mouse_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_button_release)

    def on_button_press(self, event):
        self.start_x = self.canvas.canvasx(event.x)
        self.start_y = self.canvas.canvasy(event.y)

        if self.rect:
            self.canvas.delete(self.rect)
        self.rect = self.canvas.create_rectangle(
            self.start_x, self.start_y, self.start_x, self.start_y,
            outline="red"
        )

    def on_mouse_drag(self, event):
        cur_x = self.canvas.canvasx(event.x)
        cur_y = self.canvas.canvasy(event.y)
        self.canvas.coords(self.rect, self.start_x, self.start_y, cur_x, cur_y)

    def on_button_release(self, event):
        end_x = self.canvas.canvasx(event.x)
        end_y = self.canvas.canvasy(event.y)
        self.crop_coords = (self.start_x, self.start_y, end_x, end_y)

    def save_crop(self):
        """
        Não altera a imagem de tela cheia.
        Apenas salva as coordenadas globais.
        """
        if not self.crop_coords:
            messagebox.showwarning("Aviso", "Nenhuma área selecionada para recorte.")
            return

        self.save_coordinates()
        messagebox.showinfo(
            "Coordenadas salvas",
            "As coordenadas foram salvas e serão usadas automaticamente "
            "para recortar os próximos contatos. A imagem original não foi alterada."
        )
        self.destroy()

    def save_coordinates(self):
        """
        Salva apenas o arquivo global crop_coords_config.json
        (ao lado do executável).
        """
        data = {"crop_coords": self.crop_coords}
        try:
            with open(GLOBAL_CROP_CONFIG, 'w', encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=4)
            gui_log(f"Coordenadas globais salvas em {GLOBAL_CROP_CONFIG}")
        except Exception as e:
            gui_log(f"Erro ao salvar crop_coords_config.json: {e}")


def open_crop_adjustment():
    """
    Aqui você deve escolher UMA imagem de tela cheia do WhatsApp.
    Exemplo gerado pela rotina: amostra_tela_cheia.png dentro da pasta Fotos Números.

    NÃO use as imagens já recortadas (_dados_contato.png), pois
    as coordenadas seriam relativas a um tamanho diferente.
    """
    image_path = filedialog.askopenfilename(
        title="Selecione uma imagem de TELA CHEIA do WhatsApp (por ex.: amostra_tela_cheia.png)",
        filetypes=[("Imagens", "*.png;*.jpg;*.jpeg;*.bmp;*.gif")]
    )
    if not image_path:
        return

    base_name = os.path.basename(image_path)

    if "_dados_contato" in base_name:
        messagebox.showwarning(
            "Imagem incorreta",
            "Esta imagem já está recortada (_dados_contato).\n"
            "Selecione a imagem de TELA CHEIA do WhatsApp, por exemplo "
            "amostra_tela_cheia.png, gerada pelo FotoZap."
        )
        return

    ImageCropper(root, image_path)


# =====================================================
# CONTROLES: PAUSAR / PARAR
# =====================================================

def toggle_pause():
    global pause_button

    if not processing_event.is_set():
        messagebox.showinfo("Informação", "Nenhuma captura em andamento.")
        return

    if not pause_event.is_set():
        pause_event.set()
        gui_log("Processamento pausado. Ajuste o recorte, se desejar, e depois clique em Continuar.")
        if pause_button:
            pause_button.config(text="Continuar")
    else:
        pause_event.clear()
        gui_log("Processamento retomado.")
        if pause_button:
            pause_button.config(text="Pausar")


def stop_processing():
    if not processing_event.is_set():
        messagebox.showinfo("Informação", "Nenhuma captura em andamento.")
        return

    stop_event.set()
    pause_event.clear()
    gui_log("Parada solicitada. O sistema vai concluir o contato atual, salvar o progresso e encerrar a rotina.")


# =====================================================
# GUI PRINCIPAL (WebProfile Analyzer)
# =====================================================

def build_gui():
    global root, log_text, pause_button, stop_button

    root = tk.Tk()
    root.title(APP_TITLE)
    root.state("zoomed")

    try:
        root.iconbitmap(ICON_PATH)
    except Exception as e:
        gui_log(f"Não foi possível aplicar o ícone zap.ico: {e}")

    header = tk.Frame(root, bg="#1565c0", height=60)
    header.pack(fill="x", side="top")

    title_lbl = tk.Label(
        header,
        text="WebProfile Analyzer",
        bg="#1565c0",
        fg="white",
        font=("Segoe UI Semibold", 20, "bold")
    )
    title_lbl.pack(side="left", padx=20, pady=10)

    subtitle_lbl = tk.Label(
        header,
        text="Central de ferramentas para automação de captura, processamento de dados e análise de perfis.",
        bg="#1565c0",
        fg="white",
        font=("Segoe UI", 10)
    )
    subtitle_lbl.pack(side="left", padx=10, pady=10)

    body = tk.Frame(root, bg="#f3f4f6")
    body.pack(fill="both", expand=True)

    left_col = tk.Frame(body, bg="#f3f4f6")
    left_col.pack(side="left", fill="y", padx=16, pady=16)

    def make_card(parent, title, desc, button_text=None, command=None):
        card = tk.Frame(parent, bg="white", bd=0, highlightthickness=1,
                        highlightbackground="#d1d5db")
        card.pack(fill="x", pady=(0, 12))

        tk.Label(
            card,
            text=title,
            font=("Segoe UI Semibold", 11, "bold"),
            bg="white",
            fg="#111827"
        ).pack(anchor="w", padx=12, pady=(10, 0))

        tk.Label(
            card,
            text=desc,
            font=("Segoe UI", 9),
            bg="white",
            fg="#4b5563",
            wraplength=260,
            justify="left"
        ).pack(anchor="w", padx=12, pady=(2, 8))

        if button_text and command:
            btn = tk.Button(
                card,
                text=button_text,
                font=("Segoe UI Semibold", 9),
                bg="#1565c0",
                fg="white",
                activebackground="#0b4a8f",
                activeforeground="white",
                relief="flat",
                cursor="hand2",
                padx=10,
                pady=4,
                command=command
            )
            btn.pack(anchor="w", padx=12, pady=(0, 10))

        return card

    def on_start_capture():
        if processing_event.is_set():
            messagebox.showinfo(
                "Em andamento",
                "Já existe uma captura em execução.\nPause ou pare antes de iniciar outra."
            )
            return

        folder = filedialog.askdirectory(
            title="Selecione a pasta onde está o data.json (ou as pastas de casos)"
        )
        if not folder:
            return

        def worker():
            try:
                gui_log(f"Iniciando captura de fotos na pasta: {folder}")
                execute_capture_routine(folder)
            except Exception as e:
                gui_log(f"Erro inesperado na rotina de captura (worker): {e}")
                

        threading.Thread(target=worker, daemon=True).start()

    def on_open_crop():
        open_crop_adjustment()

    def on_save_session():
        def worker():
            try:
                save_whatsapp_session()
            except Exception as e:
                gui_log(f"Erro inesperado ao salvar sessão do WhatsApp: {e}")
        threading.Thread(target=worker, daemon=True).start()

    make_card(
        left_col,
        "Capturar e processar fotos",
        "Executa o fluxo completo para buscar fotos de perfil no WhatsApp e salvar as imagens tratadas no diretório configurado.",
        "Iniciar captura de fotos",
        on_start_capture
    )

    make_card(
        left_col,
        "Ajustar recorte das fotos",
        "Use a imagem amostra_tela_cheia.png (ou outra tela cheia do WhatsApp) "
        "para calibrar o recorte do card de dados do contato.",
        "Abrir ajuste de recorte",
        on_open_crop
    )

    make_card(
        left_col,
        "Salvar sessão do WhatsApp",
        "Realiza o login no WhatsApp Web e salva a sessão localmente, evitando precisar escanear o QR code em cada execução.",
        "Salvar sessão do WhatsApp",
        on_save_session
    )

    controls_card = tk.Frame(left_col, bg="white", bd=0, highlightthickness=1,
                             highlightbackground="#d1d5db")
    controls_card.pack(fill="x", pady=(0, 12))

    tk.Label(
        controls_card,
        text="Controles da execução",
        font=("Segoe UI Semibold", 11, "bold"),
        bg="white",
        fg="#111827"
    ).pack(anchor="w", padx=12, pady=(10, 0))

    tk.Label(
        controls_card,
        text="Use Pausar para ajustar o recorte no meio do processo e Parar para encerrar a rotina com o progresso salvo.",
        font=("Segoe UI", 9),
        bg="white",
        fg="#4b5563",
        wraplength=260,
        justify="left"
    ).pack(anchor="w", padx=12, pady=(2, 8))

    buttons_frame = tk.Frame(controls_card, bg="white")
    buttons_frame.pack(anchor="w", padx=12, pady=(0, 10))

    pause_button = tk.Button(
        buttons_frame,
        text="Pausar",
        font=("Segoe UI Semibold", 9),
        bg="#f59e0b",
        fg="white",
        activebackground="#d97706",
        activeforeground="white",
        relief="flat",
        cursor="hand2",
        padx=10,
        pady=4,
        command=toggle_pause
    )
    pause_button.pack(side="left", padx=(0, 8))

    stop_button = tk.Button(
        buttons_frame,
        text="Parar",
        font=("Segoe UI Semibold", 9),
        bg="#dc2626",
        fg="white",
        activebackground="#b91c1c",
        activeforeground="white",
        relief="flat",
        cursor="hand2",
        padx=10,
        pady=4,
        command=stop_processing
    )
    stop_button.pack(side="left")

    tk.Label(
        left_col,
        text="© 2025 WebProfile Analyzer — desenvolvido por Braian Rodrigues",
        bg="#f3f4f6",
        fg="#6b7280",
        font=("Segoe UI", 8)
    ).pack(side="bottom", anchor="w", padx=4, pady=6)

    right_col = tk.Frame(body, bg="#f3f4f6")
    right_col.pack(side="left", fill="both", expand=True, padx=(0, 16), pady=16)

    log_frame = tk.Frame(right_col, bg="white", bd=0, highlightthickness=1,
                         highlightbackground="#d1d5db")
    log_frame.pack(fill="both", expand=True)

    tk.Label(
        log_frame,
        text="Atividades recentes",
        font=("Segoe UI Semibold", 11, "bold"),
        bg="white",
        fg="#111827"
    ).pack(anchor="w", padx=12, pady=(10, 0))

    log_text_widget = ScrolledText(
        log_frame,
        state=tk.DISABLED,
        bg="white",
        fg="#111827",
        font=("Consolas", 9),
        relief="flat",
        height=15
    )
    log_text_widget.pack(fill="both", expand=True, padx=12, pady=(4, 10))

    global log_text
    log_text = log_text_widget

    gui_log("Bem-vindo ao WebProfile Analyzer. Use os botões à esquerda para iniciar uma tarefa.")
    return root


# =====================================================
# MAIN
# =====================================================

if __name__ == "__main__":
    app = build_gui()
    app.mainloop()
