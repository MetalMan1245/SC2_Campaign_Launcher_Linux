# This is only the previous remove and info icons as I am still not fully convinced that the original trash an info icons fit the new UX.

    def _setup_ui(self):
        self.setFixedSize(280, 320)
        self.setStyleSheet("""
            CampaignCard { background: #2a2a2a; border-radius: 8px; border: 1px solid #3a3a3a; }
            CampaignCard:hover { border: 1px solid #6d4aff; }
        """)

        lay = QVBoxLayout(self)
        lay.setSpacing(8)
        lay.setContentsMargins(12, 12, 12, 12)

        # Cover with overlay icons (delete top-left, info top-right)
        cover = QLabel()
        cover.setFixedSize(256, 144)
        cover.setStyleSheet('background: #1a1a1a; border-radius: 4px;')
        cover.setAlignment(Qt.AlignmentFlag.AlignCenter)

        icon_style = ('QPushButton { background: rgba(0,0,0,140); color: white; '
                      'border: none; border-radius: 4px; font-size: 15px; }'
                      'QPushButton:hover { background: rgba(100,100,100,180); }')

        self.del_btn = QPushButton('🗑', cover)
        self.del_btn.setGeometry(4, 4, 28, 28)
        self.del_btn.setToolTip('Delete campaign')
        self.del_btn.setStyleSheet(icon_style)
        self.del_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.del_btn.clicked.connect(self._delete)

        self.info_btn = QPushButton('ℹ', cover)
        self.info_btn.setGeometry(256 - 32, 4, 28, 28)
        self.info_btn.setToolTip('Campaign info')
        self.info_btn.setStyleSheet(icon_style)
        self.info_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.info_btn.clicked.connect(self._info)

        if self.campaign.get('description'):
            # Rich text — HTML in mapinfo.json (e.g. <b>, <br>) renders in the tooltip
            self.info_btn.setToolTip(self.campaign['description'])

        if not self._load_cover_image(cover):
            self._placeholder(cover)
        lay.addWidget(cover, alignment=Qt.AlignmentFlag.AlignHCenter)

        # Title - CENTERED and with word wrap
        t = QLabel(self.campaign['name'])
        t.setFont(QFont('Arial', 12, QFont.Weight.Bold))
        t.setStyleSheet('color: white;')
        t.setAlignment(Qt.AlignmentFlag.AlignCenter)  # CENTERED
        t.setWordWrap(True)  # Allow wrapping for long names
        t.setMinimumHeight(40)  # Give room for multi-line text
        lay.addWidget(t)

        # Author + Version
        meta = QHBoxLayout()
        a = QLabel(f'Author: {self.campaign["author"]}')
        a.setStyleSheet('color: #999; font-size: 11px;')
        meta.addWidget(a)
        meta.addStretch()
        v = QLabel(f'v{self.campaign["version"]}')
        v.setStyleSheet('color: #999; font-size: 11px;')
        meta.addWidget(v)
        lay.addLayout(meta)

        # Status
        s = self.status_label = QLabel(self.campaign['status'].replace('_', ' ').title())
        self.status_label.setStyleSheet('color: #999; font-size: 11px; min-height: 20px;')
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setWordWrap(True)
        lay.addWidget(self.status_label)

        # Button
        btn_lay = QHBoxLayout()
        btn_lay.addStretch()
        self.btn = QPushButton()
        self.btn.setFixedSize(100, 32)
        self._style_btn()
        self.btn.clicked.connect(self._click)
        btn_lay.addWidget(self.btn)
        btn_lay.addStretch()
        lay.addLayout(btn_lay)
