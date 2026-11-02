using System.Drawing;
using System.Windows.Forms;

namespace NtBar;

internal sealed class SecondaryTaskbarForm : Form
{
    private readonly FlowLayoutPanel _windowButtons = new();
    private readonly Label _clock = new();
    private readonly System.Windows.Forms.Timer _refreshTimer = new();
    private readonly System.Windows.Forms.Timer _clockTimer = new();
    private readonly ContextMenuStrip _menu = new();
    private readonly ToolTip _toolTip = new();
    private WindowTracker? _tracker;
    private string _lastWindowSignature = string.Empty;
    private Screen _targetScreen;

    internal SecondaryTaskbarForm()
    {
        Text = "ntbar";
        FormBorderStyle = FormBorderStyle.None;
        ShowInTaskbar = false;
        StartPosition = FormStartPosition.Manual;
        TopMost = true;
        BackColor = Color.FromArgb(32, 32, 32);
        ForeColor = Color.White;
        Height = 44;
        MinimumSize = new Size(320, Height);

        _targetScreen = ChooseTargetScreen();
        BuildLayout();
        BuildMenu();

        _refreshTimer.Interval = 900;
        _refreshTimer.Tick += (_, _) => RefreshWindows();
        _clockTimer.Interval = 1000;
        _clockTimer.Tick += (_, _) => UpdateClock();

        Shown += (_, _) =>
        {
            PositionOnTargetScreen();
            _tracker = new WindowTracker(Handle);
            RefreshWindows();
            UpdateClock();
            _refreshTimer.Start();
            _clockTimer.Start();
        };

        Microsoft.Win32.SystemEvents.DisplaySettingsChanged += OnDisplaySettingsChanged;
        FormClosed += (_, _) =>
        {
            Microsoft.Win32.SystemEvents.DisplaySettingsChanged -= OnDisplaySettingsChanged;
            _refreshTimer.Dispose();
            _clockTimer.Dispose();
            _menu.Dispose();
            _toolTip.Dispose();
        };
    }

    private static Screen ChooseTargetScreen()
    {
        Screen? secondary = Screen.AllScreens.FirstOrDefault(s => !s.Primary);
        return secondary ?? Screen.PrimaryScreen ?? Screen.AllScreens[0];
    }

    private void BuildLayout()
    {
        var root = new TableLayoutPanel
        {
            Dock = DockStyle.Fill,
            ColumnCount = 3,
            RowCount = 1,
            Padding = new Padding(4, 3, 4, 3),
            BackColor = BackColor
        };
        root.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize));
        root.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
        root.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize));

        var menuButton = new Button
        {
            Text = "ntbar",
            AutoSize = true,
            FlatStyle = FlatStyle.Flat,
            ForeColor = ForeColor,
            BackColor = BackColor,
            Margin = new Padding(0, 2, 5, 2),
            Padding = new Padding(7, 0, 7, 0)
        };
        menuButton.FlatAppearance.BorderSize = 0;
        menuButton.Click += (_, _) => _menu.Show(menuButton, 0, menuButton.Height);

        _windowButtons.Dock = DockStyle.Fill;
        _windowButtons.WrapContents = false;
        _windowButtons.AutoScroll = true;
        _windowButtons.FlowDirection = FlowDirection.LeftToRight;
        _windowButtons.Margin = Padding.Empty;
        _windowButtons.BackColor = BackColor;

        _clock.AutoSize = true;
        _clock.TextAlign = ContentAlignment.MiddleCenter;
        _clock.Padding = new Padding(10, 0, 8, 0);
        _clock.Margin = Padding.Empty;
        _clock.Font = new Font("Segoe UI", 9F);

        root.Controls.Add(menuButton, 0, 0);
        root.Controls.Add(_windowButtons, 1, 0);
        root.Controls.Add(_clock, 2, 0);
        Controls.Add(root);
    }

    private void BuildMenu()
    {
        var chooseMonitor = new ToolStripMenuItem("Move bar to monitor");
        chooseMonitor.DropDownOpening += (_, _) =>
        {
            chooseMonitor.DropDownItems.Clear();
            for (int i = 0; i < Screen.AllScreens.Length; i++)
            {
                Screen screen = Screen.AllScreens[i];
                string label = $"Monitor {i + 1}{(screen.Primary ? " (primary)" : string.Empty)} — {screen.Bounds.Width}×{screen.Bounds.Height}";
                var item = new ToolStripMenuItem(label) { Checked = screen.DeviceName == _targetScreen.DeviceName };
                item.Click += (_, _) =>
                {
                    _targetScreen = screen;
                    PositionOnTargetScreen();
                };
                chooseMonitor.DropDownItems.Add(item);
            }
        };

        _menu.Items.Add(chooseMonitor);
        _menu.Items.Add(new ToolStripSeparator());
        var refresh = new ToolStripMenuItem("Refresh windows");
        refresh.Click += (_, _) => RefreshWindows();
        _menu.Items.Add(refresh);
        var exit = new ToolStripMenuItem("Exit ntbar");
        exit.Click += (_, _) => Close();
        _menu.Items.Add(exit);
    }

    private void PositionOnTargetScreen()
    {
        Rectangle bounds = _targetScreen.Bounds;
        Bounds = new Rectangle(bounds.Left, bounds.Bottom - Height, bounds.Width, Height);
    }

    private void OnDisplaySettingsChanged(object? sender, EventArgs e)
    {
        if (IsDisposed || !IsHandleCreated)
            return;

        BeginInvoke((Action)(() =>
        {
            Screen? current = Screen.AllScreens.FirstOrDefault(s => s.DeviceName == _targetScreen.DeviceName);
            _targetScreen = current ?? ChooseTargetScreen();
            PositionOnTargetScreen();
        }));
    }

    private void RefreshWindows()
    {
        if (_tracker is null || IsDisposed)
            return;

        IReadOnlyList<TrackedWindow> windows = _tracker.GetWindows();
        string signature = string.Join("|", windows.Select(w => $"{w.Handle}:{w.Title}:{w.IsMinimized}"));
        if (signature == _lastWindowSignature)
        {
            foreach (TrackedWindow window in windows)
                window.Icon?.Dispose();
            return;
        }

        _lastWindowSignature = signature;
        _windowButtons.SuspendLayout();
        try
        {
            foreach (Control control in _windowButtons.Controls)
            {
                if (control is Button button && button.Tag is TrackedWindow oldWindow)
                    oldWindow.Icon?.Dispose();
                control.Dispose();
            }
            _windowButtons.Controls.Clear();

            foreach (TrackedWindow window in windows)
            {
                var button = new Button
                {
                    Width = 150,
                    Height = 34,
                    Text = window.Title,
                    TextAlign = ContentAlignment.MiddleLeft,
                    ImageAlign = ContentAlignment.MiddleLeft,
                    TextImageRelation = TextImageRelation.ImageBeforeText,
                    FlatStyle = FlatStyle.Flat,
                    ForeColor = ForeColor,
                    BackColor = window.IsMinimized ? Color.FromArgb(42, 42, 42) : Color.FromArgb(58, 58, 58),
                    Margin = new Padding(2, 1, 2, 1),
                    Padding = new Padding(5, 0, 5, 0),
                    Tag = window
                };
                button.FlatAppearance.BorderSize = 0;
                button.FlatAppearance.MouseOverBackColor = Color.FromArgb(75, 75, 75);
                if (window.Icon is not null)
                    button.Image = window.Icon.ToBitmap();
                button.Click += (_, _) => WindowTracker.ActivateOrMinimize(window);
                button.MouseUp += (_, e) =>
                {
                    if (e.Button != MouseButtons.Right)
                        return;

                    var context = new ContextMenuStrip();
                    var closeItem = new ToolStripMenuItem("Close window");
                    closeItem.Click += (_, _) =>
                    {
                        if (NativeMethods.IsWindow(window.Handle))
                            NativeMethods.PostMessage(window.Handle, NativeMethods.WM_CLOSE, nint.Zero, nint.Zero);
                    };
                    context.Items.Add(closeItem);
                    context.Closed += (_, _) => context.Dispose();
                    context.Show(button, e.Location);
                };
                _toolTip.SetToolTip(button, window.Title);
                _windowButtons.Controls.Add(button);
            }
        }
        finally
        {
            _windowButtons.ResumeLayout();
        }
    }

    private void UpdateClock()
    {
        _clock.Text = DateTime.Now.ToString("ddd d MMM  HH:mm");
    }
}
