using System;
using System.Drawing;
using System.Windows.Forms;

namespace TappActionApp;

public class MainForm : Form
{
    private readonly AudioCapture _audio;
    private readonly HitDetector _detector;
    private readonly ActionDispatcher _dispatcher;

    private Label _banner;
    private CheckBox _chkEnable;
    private ComboBox _comboSingle;
    private ComboBox _comboDouble;
    private ComboBox _comboTriple;
    private NumericUpDown _numSpeed;
    private Button _btnPauseToggle;
    private ListView _historyList;
    private Panel _vuPanel;
    private Label _vuLabel;
    private Label _statusLabel;
    private NotifyIcon _trayIcon;
    private ContextMenuStrip _trayMenu;

    private System.Windows.Forms.Timer _bannerTimer;
    private System.Windows.Forms.Timer _uiRefreshTimer;

    private float _latestPeak = 0.0f;
    private int _serialCounter = 0;

    public MainForm()
    {
        _detector = new HitDetector();
        _dispatcher = new ActionDispatcher();
        _audio = new AudioCapture(16000, 512);

        InitializeComponent();

        _dispatcher.OnActionExecuted += Dispatcher_OnActionExecuted;
        _audio.OnAudioBlock += Audio_OnAudioBlock;

        _audio.Start();
    }

    private void InitializeComponent()
    {
        Text = "Tapp - Tap Actions (Lenovo IdeaPad)";
        Size = new Size(780, 680);
        MinimumSize = new Size(720, 580);
        BackColor = Color.White;
        ForeColor = Color.Black;
        Font = new Font("Segoe UI", 9F, FontStyle.Regular);
        StartPosition = FormStartPosition.CenterScreen;

        // Header Panel
        var pnlHeader = new Panel
        {
            Dock = DockStyle.Top,
            Height = 56,
            BackColor = Color.White,
            Padding = new Padding(14, 10, 14, 6)
        };
        pnlHeader.Paint += (s, e) =>
        {
            using var pen = new Pen(Color.Black, 1);
            e.Graphics.DrawLine(pen, 14, pnlHeader.Height - 1, pnlHeader.Width - 14, pnlHeader.Height - 1);
        };

        var lblTitle = new Label
        {
            Text = "TAPP TAP ACTIONS (C# / .NET 8 NATIVE)",
            Font = new Font("Segoe UI", 12F, FontStyle.Bold),
            ForeColor = Color.Black,
            AutoSize = true,
            Location = new Point(14, 8)
        };

        var lblSub = new Label
        {
            Text = $"Tuned Sensitivity Floor: {_detector.AmplitudeFloor:F3} | Centroid <= {_detector.CentroidMaxHz:F0} Hz | Zero-Python Native",
            Font = new Font("Segoe UI", 8.5F, FontStyle.Regular),
            ForeColor = Color.FromArgb(80, 80, 80),
            AutoSize = true,
            Location = new Point(16, 32)
        };

        pnlHeader.Controls.Add(lblTitle);
        pnlHeader.Controls.Add(lblSub);
        Controls.Add(pnlHeader);

        // Status Bar
        _statusLabel = new Label
        {
            Dock = DockStyle.Bottom,
            Height = 26,
            BackColor = Color.White,
            ForeColor = Color.FromArgb(80, 80, 80),
            TextAlign = ContentAlignment.MiddleLeft,
            Padding = new Padding(14, 0, 0, 0),
            Text = "Active: Listening for taps on palm-rest near Lenovo logo..."
        };
        Controls.Add(_statusLabel);

        // Main Content Panel
        var pnlContent = new Panel
        {
            Dock = DockStyle.Fill,
            BackColor = Color.White,
            Padding = new Padding(14, 10, 14, 6)
        };
        Controls.Add(pnlContent);

        // Build controls
        // 1. Big Visual Action Banner
        _banner = new Label
        {
            Text = "[ LISTENING FOR PALM-REST TAPS ]",
            Font = new Font("Segoe UI", 13F, FontStyle.Bold),
            TextAlign = ContentAlignment.MiddleCenter,
            BackColor = Color.White,
            ForeColor = Color.Black,
            BorderStyle = BorderStyle.FixedSingle,
            Dock = DockStyle.Top,
            Height = 50
        };

        // 2. Control Row: Master Checkbox + Pause Button
        var pnlCtrl = new Panel
        {
            Dock = DockStyle.Top,
            Height = 36,
            BackColor = Color.White,
            Padding = new Padding(0, 8, 0, 4)
        };

        _chkEnable = new CheckBox
        {
            Text = " Enable Tap Action Execution (Sends native Windows volume & media keys)",
            Font = new Font("Segoe UI", 9.5F, FontStyle.Bold),
            Checked = _dispatcher.Enabled,
            AutoSize = true,
            Location = new Point(2, 6)
        };
        _chkEnable.CheckedChanged += (s, e) =>
        {
            _dispatcher.Enabled = _chkEnable.Checked;
            _dispatcher.SaveSettings();
            _statusLabel.Text = _dispatcher.Enabled ? "Tap action execution enabled." : "Tap action execution paused.";
        };

        _btnPauseToggle = new Button
        {
            Text = "Pause Listening",
            Font = new Font("Segoe UI", 9F, FontStyle.Regular),
            FlatStyle = FlatStyle.Flat,
            BackColor = Color.White,
            ForeColor = Color.Black,
            Size = new Size(130, 28),
            Anchor = AnchorStyles.Top | AnchorStyles.Right,
            Location = new Point(pnlCtrl.Width - 130, 4)
        };
        _btnPauseToggle.Click += BtnPauseToggle_Click;

        pnlCtrl.Controls.Add(_chkEnable);
        pnlCtrl.Controls.Add(_btnPauseToggle);

        // 3. Live VU Meter Panel
        var pnlVu = new Panel
        {
            Dock = DockStyle.Top,
            Height = 32,
            BackColor = Color.White,
            Padding = new Padding(0, 4, 0, 4)
        };

        _vuPanel = new Panel
        {
            Location = new Point(2, 6),
            Size = new Size(500, 18),
            BorderStyle = BorderStyle.FixedSingle,
            BackColor = Color.White
        };
        _vuPanel.Paint += VuPanel_Paint;

        _vuLabel = new Label
        {
            Location = new Point(510, 6),
            Size = new Size(220, 18),
            Font = new Font("Consolas", 8.5F),
            ForeColor = Color.Black,
            Text = "Peak: 0.000 / Floor: 0.185"
        };

        pnlVu.Controls.Add(_vuPanel);
        pnlVu.Controls.Add(_vuLabel);

        // 4. Mappings GroupBox
        var grpMappings = new GroupBox
        {
            Text = " Gesture Mappings & Settings ",
            Font = new Font("Segoe UI", 9.5F, FontStyle.Bold),
            Dock = DockStyle.Top,
            Height = 175,
            BackColor = Color.White,
            ForeColor = Color.Black,
            Padding = new Padding(12, 10, 12, 10)
        };

        var regFont = new Font("Segoe UI", 9F, FontStyle.Regular);
        var btnFont = new Font("Segoe UI", 8.5F, FontStyle.Regular);

        // Single Tap
        var lblS = new Label { Text = "Single Tap:", Location = new Point(16, 32), Size = new Size(110, 24), AutoSize = false, Font = regFont };
        _comboSingle = new ComboBox { Location = new Point(130, 30), Size = new Size(220, 24), DropDownStyle = ComboBoxStyle.DropDownList, Font = regFont };
        _comboSingle.Items.AddRange(ActionDispatcher.AvailableActions);
        _comboSingle.SelectedItem = _dispatcher.SingleTapAction;
        _comboSingle.SelectedIndexChanged += (s, e) => SaveMappingsFromUI();

        var btnTestS = new Button { Text = "Test Vol Up", Location = new Point(360, 29), Size = new Size(115, 26), FlatStyle = FlatStyle.Flat, BackColor = Color.White, Font = btnFont };
        btnTestS.Click += (s, e) => TestAction(_comboSingle.SelectedItem?.ToString() ?? "Volume Up");

        grpMappings.Controls.Add(lblS);
        grpMappings.Controls.Add(_comboSingle);
        grpMappings.Controls.Add(btnTestS);

        // Double Tap
        var lblD = new Label { Text = "Double Tap:", Location = new Point(16, 66), Size = new Size(110, 24), AutoSize = false, Font = regFont };
        _comboDouble = new ComboBox { Location = new Point(130, 64), Size = new Size(220, 24), DropDownStyle = ComboBoxStyle.DropDownList, Font = regFont };
        _comboDouble.Items.AddRange(ActionDispatcher.AvailableActions);
        _comboDouble.SelectedItem = _dispatcher.DoubleTapAction;
        _comboDouble.SelectedIndexChanged += (s, e) => SaveMappingsFromUI();

        var btnTestD = new Button { Text = "Test Vol Down", Location = new Point(360, 63), Size = new Size(115, 26), FlatStyle = FlatStyle.Flat, BackColor = Color.White, Font = btnFont };
        btnTestD.Click += (s, e) => TestAction(_comboDouble.SelectedItem?.ToString() ?? "Volume Down");

        grpMappings.Controls.Add(lblD);
        grpMappings.Controls.Add(_comboDouble);
        grpMappings.Controls.Add(btnTestD);

        // Triple Tap
        var lblT = new Label { Text = "Triple Tap:", Location = new Point(16, 100), Size = new Size(110, 24), AutoSize = false, Font = regFont };
        _comboTriple = new ComboBox { Location = new Point(130, 98), Size = new Size(220, 24), DropDownStyle = ComboBoxStyle.DropDownList, Font = regFont };
        _comboTriple.Items.AddRange(ActionDispatcher.AvailableActions);
        _comboTriple.SelectedItem = _dispatcher.TripleTapAction;
        _comboTriple.SelectedIndexChanged += (s, e) => SaveMappingsFromUI();

        var btnTestT = new Button { Text = "Test Mute", Location = new Point(360, 97), Size = new Size(115, 26), FlatStyle = FlatStyle.Flat, BackColor = Color.White, Font = btnFont };
        btnTestT.Click += (s, e) => TestAction(_comboTriple.SelectedItem?.ToString() ?? "Mute / Unmute Volume");

        grpMappings.Controls.Add(lblT);
        grpMappings.Controls.Add(_comboTriple);
        grpMappings.Controls.Add(btnTestT);

        // Speed Window
        var lblSpeed = new Label { Text = "Double-Tap Window:", Location = new Point(16, 136), Size = new Size(130, 24), AutoSize = false, Font = regFont };
        _numSpeed = new NumericUpDown
        {
            Location = new Point(150, 134),
            Size = new Size(60, 24),
            DecimalPlaces = 2,
            Increment = 0.02M,
            Minimum = 0.20M,
            Maximum = 0.70M,
            Font = regFont,
            Value = (decimal)_dispatcher.DoubleTapTimeoutSec
        };
        _numSpeed.ValueChanged += (s, e) => SaveMappingsFromUI();

        var lblSec = new Label { Text = "seconds (Single tap waits window before firing; Double tap fires immediately)", Location = new Point(220, 136), Size = new Size(480, 24), ForeColor = Color.FromArgb(80, 80, 80), Font = new Font("Segoe UI", 8.5F) };

        grpMappings.Controls.Add(lblSpeed);
        grpMappings.Controls.Add(_numSpeed);
        grpMappings.Controls.Add(lblSec);

        // 5. History Log
        var grpHistory = new GroupBox
        {
            Text = " Action Execution History Log ",
            Font = new Font("Segoe UI", 9.5F, FontStyle.Bold),
            Dock = DockStyle.Fill,
            BackColor = Color.White,
            ForeColor = Color.Black,
            Padding = new Padding(10, 8, 10, 8)
        };

        var btnClearHist = new Button
        {
            Text = "Clear Log",
            Font = new Font("Segoe UI", 8.5F),
            FlatStyle = FlatStyle.Flat,
            BackColor = Color.White,
            Size = new Size(80, 23),
            Location = new Point(grpHistory.Width - 95, 12),
            Anchor = AnchorStyles.Top | AnchorStyles.Right
        };
        btnClearHist.Click += (s, e) => _historyList.Items.Clear();

        _historyList = new ListView
        {
            Dock = DockStyle.Fill,
            View = View.Details,
            FullRowSelect = true,
            GridLines = true,
            Font = new Font("Consolas", 9F),
            BackColor = Color.White,
            ForeColor = Color.Black,
            BorderStyle = BorderStyle.FixedSingle
        };
        _historyList.Columns.Add("Serial", 65);
        _historyList.Columns.Add("Time", 90);
        _historyList.Columns.Add("Gesture Detected", 160);
        _historyList.Columns.Add("Dispatched Windows Action", 320);

        grpHistory.Controls.Add(btnClearHist);
        grpHistory.Controls.Add(_historyList);

        // In Windows Forms, controls with Dock=Top are stacked in reverse of addition order.
        // Adding them from bottom to top ensures the correct top-to-bottom layout without clipping.
        pnlContent.Controls.Add(grpHistory);
        pnlContent.Controls.Add(grpMappings);
        pnlContent.Controls.Add(pnlVu);
        pnlContent.Controls.Add(pnlCtrl);
        pnlContent.Controls.Add(_banner);

        // Timers
        _bannerTimer = new System.Windows.Forms.Timer { Interval = 700 };
        _bannerTimer.Tick += (s, e) =>
        {
            _bannerTimer.Stop();
            _banner.BackColor = Color.White;
            _banner.ForeColor = Color.Black;
            _banner.Text = _audio.IsRecording ? "[ LISTENING FOR PALM-REST TAPS ]" : "[ PAUSED - NOT LISTENING ]";
        };

        _uiRefreshTimer = new System.Windows.Forms.Timer { Interval = 40 };
        _uiRefreshTimer.Tick += (s, e) =>
        {
            _vuPanel.Invalidate();
            _vuLabel.Text = $"Peak: {_latestPeak:F3} / Floor: {_detector.AmplitudeFloor:F3}";
        };
        _uiRefreshTimer.Start();

        // System Tray
        SetupSystemTray();
    }

    private void SetupSystemTray()
    {
        _trayMenu = new ContextMenuStrip();
        _trayMenu.Items.Add("Open Tapp Actions", null, (s, e) => { Show(); WindowState = FormWindowState.Normal; BringToFront(); });
        _trayMenu.Items.Add("Toggle Listening", null, (s, e) => BtnPauseToggle_Click(s, e));
        _trayMenu.Items.Add("-");
        _trayMenu.Items.Add("Exit", null, (s, e) => { _trayIcon.Visible = false; Application.Exit(); });

        _trayIcon = new NotifyIcon
        {
            Text = "Tapp Tap Actions (Lenovo IdeaPad)",
            Icon = SystemIcons.Application,
            ContextMenuStrip = _trayMenu,
            Visible = true
        };
        _trayIcon.DoubleClick += (s, e) => { Show(); WindowState = FormWindowState.Normal; BringToFront(); };
    }

    private void VuPanel_Paint(object? sender, PaintEventArgs e)
    {
        int w = _vuPanel.Width;
        int h = _vuPanel.Height;

        e.Graphics.Clear(Color.White);

        int barW = (int)(w * Math.Clamp(_latestPeak * 2.5f, 0.0f, 1.0f));
        if (barW > 0)
        {
            using var brush = new SolidBrush(Color.Black);
            e.Graphics.FillRectangle(brush, 0, 0, barW, h);
        }

        // Draw Floor Threshold Line
        int lineX = (int)(w * Math.Clamp(_detector.AmplitudeFloor * 2.5f, 0.0f, 1.0f));
        using var linePen = new Pen(Color.FromArgb(120, 120, 120), 2);
        e.Graphics.DrawLine(linePen, lineX, 0, lineX, h);
    }

    private void Audio_OnAudioBlock(float[] block)
    {
        double nowSec = DateTime.UtcNow.Ticks / (double)TimeSpan.TicksPerSecond;
        bool isHit = _detector.ProcessBlock(block, nowSec, out float conf, out float peak, out float centroid, out float attack);

        _latestPeak = peak;

        if (isHit)
        {
            _dispatcher.OnHitDetected();
        }
    }

    private void Dispatcher_OnActionExecuted(string gesture, string action)
    {
        if (InvokeRequired)
        {
            BeginInvoke(new Action(() => Dispatcher_OnActionExecuted(gesture, action)));
            return;
        }

        _serialCounter++;
        string timeStr = DateTime.Now.ToString("HH:mm:ss");

        var item = new ListViewItem($"#{_serialCounter:D2}");
        item.SubItems.Add(timeStr);
        item.SubItems.Add(gesture.ToUpper());
        item.SubItems.Add(action);
        _historyList.Items.Add(item);
        item.EnsureVisible();

        // Flash High-Contrast Banner
        _bannerTimer.Stop();
        _banner.BackColor = Color.Black;
        _banner.ForeColor = Color.White;
        _banner.Text = $"{gesture.ToUpper()} -> {action}";
        _bannerTimer.Start();

        _statusLabel.Text = $"Executed: {gesture} -> {action} @ {timeStr}";

        if (WindowState == FormWindowState.Minimized)
        {
            _trayIcon.ShowBalloonTip(1200, "Tapp Action", $"{gesture} -> {action}", ToolTipIcon.Info);
        }
    }

    private void TestAction(string action)
    {
        ActionDispatcher.Execute(action);
        _serialCounter++;
        string timeStr = DateTime.Now.ToString("HH:mm:ss");

        var item = new ListViewItem($"#{_serialCounter:D2}");
        item.SubItems.Add(timeStr);
        item.SubItems.Add("[TEST] TRIGGER");
        item.SubItems.Add(action);
        _historyList.Items.Add(item);
        item.EnsureVisible();

        _statusLabel.Text = $"Test executed: {action}";
    }

    private void SaveMappingsFromUI()
    {
        _dispatcher.SingleTapAction = _comboSingle.SelectedItem?.ToString() ?? _dispatcher.SingleTapAction;
        _dispatcher.DoubleTapAction = _comboDouble.SelectedItem?.ToString() ?? _dispatcher.DoubleTapAction;
        _dispatcher.TripleTapAction = _comboTriple.SelectedItem?.ToString() ?? _dispatcher.TripleTapAction;
        _dispatcher.DoubleTapTimeoutSec = (float)_numSpeed.Value;
        _dispatcher.SaveSettings();
    }

    private void BtnPauseToggle_Click(object? sender, EventArgs e)
    {
        if (_audio.IsRecording)
        {
            _audio.Stop();
            _btnPauseToggle.Text = "Resume Listening";
            _banner.Text = "[ PAUSED - NOT LISTENING ]";
            _statusLabel.Text = "Audio stream paused.";
        }
        else
        {
            _audio.Start();
            _btnPauseToggle.Text = "Pause Listening";
            _banner.Text = "[ LISTENING FOR PALM-REST TAPS ]";
            _statusLabel.Text = "Listening for palm-rest taps...";
        }
    }

    protected override void OnResize(EventArgs e)
    {
        base.OnResize(e);
        if (WindowState == FormWindowState.Minimized)
        {
            Hide();
        }
    }

    protected override void OnFormClosing(FormClosingEventArgs e)
    {
        _trayIcon.Visible = false;
        _audio.Dispose();
        base.OnFormClosing(e);
    }
}
