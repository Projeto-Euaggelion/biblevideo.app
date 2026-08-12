"""
Editor de thumbnail nativo (Tkinter + Pillow).

Abre uma janela de desktop na máquina que roda o servidor para montar a
thumbnail do vídeo: uma imagem de fundo (importada e recortada para
preencher o quadro, no formato do vídeo) e textos posicionáveis sobre ela.
Isso só faz sentido porque esta aplicação é de uso local/pessoal — quem
clica no botão é a mesma pessoa que está rodando o servidor.

A chamada bloqueia até a janela ser fechada (salvando ou cancelando), o que
é aceitável aqui porque cada rota síncrona do FastAPI já roda numa thread
própria do threadpool: bloquear essa thread não trava o resto da aplicação.
"""

import threading
from pathlib import Path
from typing import Optional

import tkinter as tk
from tkinter import colorchooser, filedialog, messagebox, simpledialog

from PIL import Image, ImageDraw, ImageFont, ImageTk

PREVIEW_MAX_W = 900
PREVIEW_MAX_H = 620

# Fontes TrueType comuns, tentadas em ordem — cai para a fonte padrão do
# Pillow (bitmap, sem escala) se nenhuma existir no sistema.
_FONT_CANDIDATES = [
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
]

_editor_lock = threading.Lock()


class ThumbnailEditorBusyError(Exception):
    """Já existe uma sessão do editor de thumbnail aberta neste processo."""
    pass


def _load_font(size: int) -> ImageFont.ImageFont:
    for path in _FONT_CANDIDATES:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


class _TextItem:
    def __init__(self, text: str, x: float, y: float, font_size: int, color: str):
        self.text = text
        self.x = x  # coordenadas no espaço do canvas de preview (não no da imagem final)
        self.y = y
        self.font_size = font_size
        self.color = color
        self.canvas_id: Optional[int] = None


class _ThumbnailEditorWindow:
    def __init__(self, target_size: tuple[int, int], output_path: Path, initial_image_path: Optional[str]):
        self.target_w, self.target_h = target_size
        self.output_path = Path(output_path)

        self.scale = min(PREVIEW_MAX_W / self.target_w, PREVIEW_MAX_H / self.target_h, 1.0)
        self.canvas_w = max(1, round(self.target_w * self.scale))
        self.canvas_h = max(1, round(self.target_h * self.scale))

        self.bg_image_path: Optional[str] = None
        self.bg_color = "#1a1a1a"
        self.texts: list[_TextItem] = []
        self.saved = False

        self._bg_photo = None  # referência forte p/ o PhotoImage não ser coletado pelo GC
        self._drag_item: Optional[_TextItem] = None
        self._drag_offset = (0, 0)
        self._selected: Optional[_TextItem] = None

        self.root = tk.Tk()
        self.root.title("Editor de Thumbnail")
        self.root.resizable(False, False)
        self.root.protocol("WM_DELETE_WINDOW", self._on_cancel)

        self._build_ui()

        if initial_image_path and Path(initial_image_path).exists():
            self.bg_image_path = initial_image_path
        self._redraw_background()

    # ---- construção da UI ----

    def _build_ui(self) -> None:
        container = tk.Frame(self.root, padx=12, pady=12)
        container.pack(fill="both", expand=True)

        self.canvas = tk.Canvas(
            container, width=self.canvas_w, height=self.canvas_h,
            bg=self.bg_color, highlightthickness=1, highlightbackground="#444",
        )
        self.canvas.grid(row=0, column=0, rowspan=11, padx=(0, 16))

        tk.Label(container, text="Editor de Thumbnail", font=("Segoe UI", 13, "bold")).grid(
            row=0, column=1, sticky="w", pady=(0, 4)
        )
        tk.Label(
            container,
            text=f"Tamanho final: {self.target_w}x{self.target_h}px",
            fg="#666",
        ).grid(row=1, column=1, sticky="w", pady=(0, 10))

        tk.Button(container, text="Importar imagem de fundo", width=28, command=self._on_import_image).grid(
            row=2, column=1, sticky="w", pady=3
        )
        tk.Button(container, text="Remover imagem de fundo", width=28, command=self._on_remove_image).grid(
            row=3, column=1, sticky="w", pady=3
        )
        tk.Button(container, text="Cor de fundo...", width=28, command=self._on_pick_bg_color).grid(
            row=4, column=1, sticky="w", pady=3
        )

        tk.Frame(container, height=2, bd=1, relief="sunken").grid(row=5, column=1, sticky="ew", pady=10)

        tk.Button(container, text="+ Adicionar texto", width=28, command=self._on_add_text).grid(
            row=6, column=1, sticky="w", pady=3
        )
        tk.Button(container, text="Editar texto selecionado", width=28, command=self._on_edit_text).grid(
            row=7, column=1, sticky="w", pady=3
        )
        tk.Button(container, text="Cor do texto selecionado...", width=28, command=self._on_pick_text_color).grid(
            row=8, column=1, sticky="w", pady=3
        )

        size_frame = tk.Frame(container)
        size_frame.grid(row=9, column=1, sticky="w", pady=3)
        tk.Label(size_frame, text="Tamanho da fonte:").pack(side="left")
        self.size_var = tk.IntVar(value=48)
        tk.Spinbox(
            size_frame, from_=10, to=300, width=5, textvariable=self.size_var, command=self._on_size_change
        ).pack(side="left", padx=6)

        tk.Button(container, text="Excluir texto selecionado", width=28, command=self._on_delete_text).grid(
            row=10, column=1, sticky="w", pady=3
        )

        tk.Label(
            container,
            text="Dica: arraste um texto no quadro para reposicioná-lo.\nDê um duplo clique para editar o conteúdo.",
            fg="#666", justify="left",
        ).grid(row=11, column=1, sticky="w", pady=(10, 0))

        actions = tk.Frame(container)
        actions.grid(row=12, column=1, sticky="ew", pady=(20, 0))
        tk.Button(actions, text="Salvar", command=self._on_save, bg="#2e7d32", fg="white", width=13).pack(
            side="left", padx=(0, 8)
        )
        tk.Button(actions, text="Cancelar", command=self._on_cancel, width=13).pack(side="left")

        self.canvas.bind("<ButtonPress-1>", self._on_canvas_press)
        self.canvas.bind("<B1-Motion>", self._on_canvas_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_canvas_release)
        self.canvas.bind("<Double-Button-1>", self._on_canvas_double_click)

    # ---- fundo ----

    def _on_import_image(self) -> None:
        path = filedialog.askopenfilename(
            title="Selecione a imagem de fundo",
            filetypes=[("Imagens", "*.png *.jpg *.jpeg *.webp *.bmp"), ("Todos os arquivos", "*.*")],
            parent=self.root,
        )
        if path:
            self.bg_image_path = path
            self._redraw_background()

    def _on_remove_image(self) -> None:
        self.bg_image_path = None
        self._redraw_background()

    def _on_pick_bg_color(self) -> None:
        color = colorchooser.askcolor(color=self.bg_color, title="Cor de fundo", parent=self.root)
        if color and color[1]:
            self.bg_color = color[1]
            if not self.bg_image_path:
                self._redraw_background()

    @staticmethod
    def _cover_crop(img: Image.Image, box_w: int, box_h: int) -> Image.Image:
        """Redimensiona a imagem para cobrir totalmente a caixa (box_w x box_h),
        recortando o excedente centralizado — equivalente a `background-size: cover`."""
        img_w, img_h = img.size
        scale = max(box_w / img_w, box_h / img_h)
        new_w, new_h = max(1, round(img_w * scale)), max(1, round(img_h * scale))
        resized = img.resize((new_w, new_h), Image.LANCZOS)
        left = (new_w - box_w) // 2
        top = (new_h - box_h) // 2
        return resized.crop((left, top, left + box_w, top + box_h))

    def _redraw_background(self) -> None:
        self.canvas.delete("bg")
        if self.bg_image_path:
            try:
                img = Image.open(self.bg_image_path).convert("RGB")
                preview = self._cover_crop(img, self.canvas_w, self.canvas_h)
                self._bg_photo = ImageTk.PhotoImage(preview)
                self.canvas.create_image(0, 0, anchor="nw", image=self._bg_photo, tags="bg")
            except Exception as e:
                messagebox.showerror("Erro", f"Não foi possível carregar a imagem: {e}", parent=self.root)
                self.bg_image_path = None
                self.canvas.configure(bg=self.bg_color)
        else:
            self._bg_photo = None
            self.canvas.configure(bg=self.bg_color)
        self.canvas.tag_lower("bg")
        for item in self.texts:
            self._draw_text_item(item)

    # ---- texto ----

    def _draw_text_item(self, item: _TextItem) -> None:
        if item.canvas_id is not None:
            self.canvas.delete(item.canvas_id)
        item.canvas_id = self.canvas.create_text(
            item.x, item.y, text=item.text, fill=item.color,
            font=("Segoe UI", item.font_size, "bold"),
        )

    def _on_add_text(self) -> None:
        text = simpledialog.askstring("Novo texto", "Texto:", initialvalue="Título", parent=self.root)
        if not text or not text.strip():
            return
        item = _TextItem(text.strip(), self.canvas_w // 2, self.canvas_h // 2, self.size_var.get(), "#ffffff")
        self.texts.append(item)
        self._draw_text_item(item)
        self._select(item)

    def _on_edit_text(self) -> None:
        if not self._selected:
            messagebox.showinfo(
                "Nenhum texto selecionado", "Clique em um texto no quadro para selecioná-lo.", parent=self.root
            )
            return
        text = simpledialog.askstring(
            "Editar texto", "Texto:", initialvalue=self._selected.text, parent=self.root
        )
        if text is not None and text.strip():
            self._selected.text = text.strip()
            self._draw_text_item(self._selected)

    def _on_delete_text(self) -> None:
        if not self._selected:
            return
        self.canvas.delete(self._selected.canvas_id)
        self.texts.remove(self._selected)
        self._selected = None

    def _on_pick_text_color(self) -> None:
        if not self._selected:
            messagebox.showinfo(
                "Nenhum texto selecionado", "Clique em um texto no quadro para selecioná-lo.", parent=self.root
            )
            return
        color = colorchooser.askcolor(color=self._selected.color, title="Cor do texto", parent=self.root)
        if color and color[1]:
            self._selected.color = color[1]
            self._draw_text_item(self._selected)

    def _on_size_change(self) -> None:
        if self._selected:
            self._selected.font_size = self.size_var.get()
            self._draw_text_item(self._selected)

    def _select(self, item: Optional[_TextItem]) -> None:
        self._selected = item
        if item:
            self.size_var.set(item.font_size)

    def _item_at(self, x: float, y: float) -> Optional[_TextItem]:
        found = self.canvas.find_closest(x, y)
        for item in self.texts:
            if item.canvas_id in found:
                return item
        return None

    # ---- interação do canvas ----

    def _on_canvas_press(self, event) -> None:
        item = self._item_at(event.x, event.y)
        self._select(item)
        if item:
            self._drag_item = item
            self._drag_offset = (event.x - item.x, event.y - item.y)

    def _on_canvas_drag(self, event) -> None:
        if self._drag_item:
            item = self._drag_item
            item.x = max(0, min(self.canvas_w, event.x - self._drag_offset[0]))
            item.y = max(0, min(self.canvas_h, event.y - self._drag_offset[1]))
            self.canvas.coords(item.canvas_id, item.x, item.y)

    def _on_canvas_release(self, _event) -> None:
        self._drag_item = None

    def _on_canvas_double_click(self, event) -> None:
        item = self._item_at(event.x, event.y)
        if item:
            self._select(item)
            self._on_edit_text()

    # ---- salvar / renderização final ----

    def _render_full_resolution(self) -> Image.Image:
        if self.bg_image_path:
            try:
                img = Image.open(self.bg_image_path).convert("RGB")
                final = self._cover_crop(img, self.target_w, self.target_h)
            except Exception:
                final = Image.new("RGB", (self.target_w, self.target_h), self.bg_color)
        else:
            final = Image.new("RGB", (self.target_w, self.target_h), self.bg_color)

        draw = ImageDraw.Draw(final)
        inv_scale = (1 / self.scale) if self.scale else 1.0

        for item in self.texts:
            full_size = max(8, round(item.font_size * inv_scale))
            font = _load_font(full_size)
            fx, fy = item.x * inv_scale, item.y * inv_scale
            bbox = draw.textbbox((0, 0), item.text, font=font)
            tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
            draw.text((fx - tw / 2, fy - th / 2), item.text, font=font, fill=item.color)

        return final

    def _on_save(self) -> None:
        try:
            final = self._render_full_resolution()
            self.output_path.parent.mkdir(parents=True, exist_ok=True)
            final.save(self.output_path, "PNG")
            self.saved = True
            self.root.destroy()
        except Exception as e:
            messagebox.showerror("Erro ao salvar", str(e), parent=self.root)

    def _on_cancel(self) -> None:
        self.saved = False
        self.root.destroy()

    def run(self) -> bool:
        self.root.mainloop()
        return self.saved


def open_thumbnail_editor(
    target_size: tuple[int, int],
    output_path,
    initial_image_path: Optional[str] = None,
) -> bool:
    """
    Abre a janela do editor de thumbnail e bloqueia até o usuário fechá-la.

    Args:
        target_size: (largura, altura) finais da thumbnail, seguindo o
            formato do vídeo (paisagem ou vertical).
        output_path: caminho onde a thumbnail deve ser salva ao clicar em Salvar.
        initial_image_path: thumbnail já existente, usada como imagem de
            fundo inicial ao reabrir o editor.

    Returns:
        True se o usuário salvou uma thumbnail, False se cancelou.

    Raises:
        ThumbnailEditorBusyError: se já houver uma sessão do editor aberta
            neste processo (Tkinter não permite múltiplos mainloops
            simultâneos com segurança).
    """
    if not _editor_lock.acquire(blocking=False):
        raise ThumbnailEditorBusyError("Já existe uma janela do editor de thumbnail aberta.")
    try:
        window = _ThumbnailEditorWindow(target_size, Path(output_path), initial_image_path)
        return window.run()
    finally:
        _editor_lock.release()
