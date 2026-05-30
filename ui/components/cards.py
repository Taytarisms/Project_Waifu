import customtkinter as ctk
from files.ui import theme
from PIL import Image, ImageDraw

def _rounded_pill(img: Image.Image, radius: int = 10) -> Image.Image:
    img = img.convert("RGBA")
    mask = Image.new("L", img.size, 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle((0, 0, *img.size), radius=radius, fill=255)
    result = Image.new("RGBA", img.size, (0, 0, 0, 0))
    result.paste(img, mask=mask)
    return result

def _make_card_tile(
    icon: Image.Image,
    card_w: int,
    card_h: int,
    bg: tuple = (30, 46, 64, 230),
    border: tuple = (80, 120, 160, 180),
    radius: int = 9,
) -> Image.Image:
    card = Image.new("RGBA", (card_w, card_h), (0, 0, 0, 0))
    # fill
    body = Image.new("RGBA", (card_w, card_h), bg)
    body_mask = Image.new("L", (card_w, card_h), 0)
    ImageDraw.Draw(body_mask).rounded_rectangle((0, 0, card_w - 1, card_h - 1), radius=radius, fill=255)
    card.paste(body, mask=body_mask)
    # border
    border_layer = Image.new("RGBA", (card_w, card_h), (0, 0, 0, 0))
    ImageDraw.Draw(border_layer).rounded_rectangle(
        (0, 0, card_w - 1, card_h - 1),
        radius=radius,
        outline=border,
        width=1,
    )
    card = Image.alpha_composite(card, border_layer)
    # icon centred
    ix = (card_w - icon.width)  // 2
    iy = (card_h - icon.height) // 2
    card.paste(icon, (ix, iy), icon)
    return card

def _rotate_pil(img: Image.Image, angle_deg: float) -> Image.Image:
    return img.rotate(-angle_deg, resample=Image.BICUBIC, expand=True)

def _build_fan_composite(
    icon_paths: list,
    canvas_w: int = 300,
    canvas_h: int = 110,
    card_w: int  = 56,
    card_h: int  = 74,
    icon_size: int = 34,
    max_angle: float = 22.0,
    y_sink: float = 0.55,
    x_spread: float = 0.72,       
) -> Image.Image:
    canvas = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
    n = len(icon_paths)
    if n == 0:
        return canvas

    # Load + resize icons
    icons = []
    for p in icon_paths:
        try:
            ico = Image.open(p).convert("RGBA")
            ico.thumbnail((icon_size, icon_size), Image.LANCZOS)
            icons.append(ico)
        except Exception:
            icons.append(Image.new("RGBA", (icon_size, icon_size), (0, 0, 0, 0)))

    # Fan position
    cx = canvas_w // 2
    base_y = int(canvas_h * y_sink)

    edge_pad = max(card_w // 2 + 14, 36)
    usable_w = max(canvas_w - edge_pad * 2, 40)
    spread_half = int((usable_w * x_spread) / 2)

    if n == 1:
        positions = [(cx, base_y, 0.0)]
    else:
        positions = []
        for i, _icon in enumerate(icons):
            t = (i / (n - 1)) * 2 - 1          # -1 to +1
            x = cx + int(t * spread_half)
            angle = t * max_angle
            # sink outer cards a bit so fan looks natural
            y = base_y + int(abs(t) * 8)
            positions.append((x, y, angle))

    order = list(range(n))
    centre_idx = n // 2
    order.sort(key=lambda i: -abs(i - centre_idx))

    for i in order:
        x, y, angle = positions[i]
        tile = _make_card_tile(icons[i], card_w, card_h)
        rotated = _rotate_pil(tile, angle)
        px = x - rotated.width  // 2
        py = y - rotated.height
        canvas.paste(rotated, (px, py), rotated)

    return canvas

class ProviderCard(ctk.CTkFrame):
    def __init__(
        self,
        master,
        name,
        description,
        status="Inactive",
        image_path=None,
        command=None,
    ):
        super().__init__(
            master,
            fg_color=theme.CARD_BG,
            corner_radius=10,
            border_width=1,
            border_color="#3d566e",
        )

        self.command = command
        self.grid_columnconfigure(0, weight=1)
        self.is_active = False

        self.normal_color  = theme.CARD_BG
        self.hover_color   = "#3a6f90"
        self.active_color  = "#2d5874"
        self.normal_border = "#3d566e"
        self.hover_border  = theme.ACCENT
        self.active_border = theme.ACCENT

        title_row = 0

        image_paths = (
            image_path if isinstance(image_path, list)
            else ([image_path] if image_path else [])
        )

        if image_paths:
            try:
                self.logo_well = ctk.CTkFrame(
                    self,
                    fg_color="#1b2838",
                    corner_radius=8,
                    height=192,
                    border_width=1,
                    border_color="#45627a",
                )
                self.logo_well.configure(bg_color=theme.CARD_BG)
                self.logo_well.grid(
                    row=0, column=0,
                    sticky="ew",
                    padx=14, pady=(14, 10),
                )
                self.logo_well.grid_propagate(False)
                self.logo_well.grid_columnconfigure(0, weight=1)
                self.logo_well.grid_rowconfigure(0, weight=1)

                if len(image_paths) == 1:
                    img = Image.open(image_paths[0]).convert("RGBA")
                    img.thumbnail((260, 86), Image.LANCZOS)
                    self.card_image = ctk.CTkImage(
                        light_image=img, dark_image=img, size=img.size
                    )
                    self.image_label = ctk.CTkLabel(
                        self.logo_well, image=self.card_image,
                        text="", fg_color="#1b2838",
                    )
                    self.image_label.grid(row=0, column=0)
                    self.image_label.bind("<Button-1>", self._clicked)
                    self.image_label.bind("<Enter>",    self._on_hover)
                    self.image_label.bind("<Leave>",    self._on_leave)

                else:
                    composite = _build_fan_composite(
                        image_paths,
                        canvas_w  = 480,
                        canvas_h  = 190,
                        card_w    = 92,
                        card_h    = 114,
                        icon_size = 76,
                        max_angle = 20.0,
                        y_sink    = 0.88,
                        x_spread  = 0.78,
                    )
                    self._fan_ctk = ctk.CTkImage(
                        light_image=composite,
                        dark_image=composite,
                        size=(composite.width, composite.height),
                    )
                    self.image_label = ctk.CTkLabel(
                        self.logo_well,
                        image=self._fan_ctk,
                        text="",
                        fg_color="transparent",
                    )
                    self.image_label.pack(expand=True, padx=10, pady=8)
                    self.image_label.bind("<Button-1>", self._clicked)
                    self.image_label.bind("<Enter>",    self._on_hover)
                    self.image_label.bind("<Leave>",    self._on_leave)

                self.logo_well.bind("<Button-1>", self._clicked)
                self.logo_well.bind("<Enter>",    self._on_hover)
                self.logo_well.bind("<Leave>",    self._on_leave)

                title_row = 1

            except Exception as e:
                print(f"Failed to load provider image for {name}: {e}")
                title_row = 0

        self.title = ctk.CTkLabel(
            self,
            text=name,
            font=ctk.CTkFont(size=22, weight="bold"),
            text_color=theme.TEXT,
            anchor="w",
        )
        self.title.grid(row=title_row, column=0, sticky="ew", padx=18, pady=(4, 4))

        self.desc = ctk.CTkLabel(
            self,
            text=description,
            text_color=theme.MUTED_TEXT,
            anchor="w",
            justify="left",
            wraplength=460,
        )
        self.desc.grid(row=title_row + 1, column=0, sticky="ew", padx=18, pady=(0, 12))

        self.status = ctk.CTkLabel(
            self,
            text=f"Status: {status}",
            text_color=theme.ACCENT,
            anchor="w",
        )
        self.status.grid(row=title_row + 2, column=0, sticky="ew", padx=18, pady=(0, 18))

        for widget in (self, self.title, self.desc, self.status):
            widget.bind("<Button-1>", self._clicked)
            widget.bind("<Enter>",    self._on_hover)
            widget.bind("<Leave>",    self._on_leave)

        if hasattr(self, "logo_well"):
            self.logo_well.bind("<Button-1>", self._clicked)
            self.logo_well.bind("<Enter>",    self._on_hover)
            self.logo_well.bind("<Leave>",    self._on_leave)

        if hasattr(self, "image_label"):
            self.image_label.bind("<Button-1>", self._clicked)
            self.image_label.bind("<Enter>",    self._on_hover)
            self.image_label.bind("<Leave>",    self._on_leave)

    def set_active(self, active: bool):
        self.is_active = active
        self.configure(
            border_color=self.active_border if active else self.normal_border,
            border_width=2 if active else 1,
            fg_color=self.active_color if active else self.normal_color,
        )
        if hasattr(self, "logo_well"):
            self.logo_well.configure(
                border_color=theme.ACCENT if active else "#45627a",
                fg_color="#21384b" if active else "#1b2838",
            )
        self.status.configure(
            text="Status: Active" if active else "Status: Inactive",
            text_color=theme.SUCCESS if active else theme.ACCENT,
        )

    def _on_hover(self, _event=None):
        self.configure(
            fg_color=("#417da3" if self.is_active else self.hover_color),
            border_color=theme.ACCENT,
            border_width=2,
        )
        if hasattr(self, "logo_well"):
            self.logo_well.configure(fg_color="#243f55", border_color=theme.ACCENT)

    def _on_leave(self, _event=None):
        self.set_active(self.is_active)

    def _clicked(self, _event=None):
        if self.command:
            self.command()