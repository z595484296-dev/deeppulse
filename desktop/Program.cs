using System.Diagnostics;
using System.Net;
using System.Text.Json;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

namespace DeepSeekHarnessDesktop;

internal static class Program
{
    [STAThread]
    private static void Main()
    {
        using var mutex = new Mutex(true, @"Local\DeepSeekHarnessDesktop", out var isFirstInstance);
        if (!isFirstInstance)
        {
            MessageBox.Show(
                "深脉 DeepPulse 已经在运行。",
                "DeepSeek Harness Desktop",
                MessageBoxButtons.OK,
                MessageBoxIcon.Information);
            return;
        }

        ApplicationConfiguration.Initialize();
        Application.Run(new HarnessForm());
    }
}

internal sealed class HarnessForm : Form
{
    private static readonly Uri HarnessUri = new("http://127.0.0.1:3080/");
    private static readonly Version MinimumDeepPulseVersion = new(1, 7, 0);
    private static readonly int[] DeepPulsePorts = Enumerable.Range(8971, 10).ToArray();
    private static readonly Color Background = Color.FromArgb(11, 15, 25);
    private static readonly Color PanelBackground = Color.FromArgb(18, 24, 38);
    private static readonly Color MutedText = Color.FromArgb(159, 170, 190);
    private static readonly Color Accent = Color.FromArgb(92, 84, 255);

    private readonly HttpClient httpClient = new() { Timeout = TimeSpan.FromSeconds(3) };
    private readonly WebView2 webView = new() { Dock = DockStyle.Fill, Visible = false };
    private readonly Panel splash = new() { Dock = DockStyle.Fill, BackColor = Background };
    private readonly Label statusLabel = new()
    {
        AutoSize = false,
        ForeColor = MutedText,
        Font = new Font("Microsoft YaHei UI", 10.5f),
        TextAlign = ContentAlignment.MiddleCenter,
        Width = 440,
        Height = 54,
        Text = "正在唤醒 DeepSeek Harness…"
    };
    private readonly ProgressBar progress = new()
    {
        Style = ProgressBarStyle.Marquee,
        MarqueeAnimationSpeed = 28,
        Width = 300,
        Height = 4
    };
    private readonly FlowLayoutPanel actions = new()
    {
        AutoSize = true,
        FlowDirection = FlowDirection.LeftToRight,
        WrapContents = false,
        Visible = false,
        BackColor = Color.Transparent
    };

    private Process? ownedBackend;
    private Process? ownedDeepPulse;
    private HarnessInstallation? installation;
    private DeepPulseInstallation? deepPulseInstallation;
    private Uri? activeDeepPulseBaseUri;
    private string? deepPulseBootstrapScriptId;
    private CancellationTokenSource? startupCancellation;
    private readonly string dataDirectory;
    private readonly string logPath;
    private readonly object logLock = new();
    private bool startupInProgress;

    internal HarnessForm()
    {
        Text = "深脉 DeepPulse · DeepSeek Harness";
        BackColor = Background;
        ForeColor = Color.White;
        StartPosition = FormStartPosition.CenterScreen;
        MinimumSize = new Size(900, 640);
        Size = new Size(1320, 860);
        KeyPreview = true;

        try
        {
            Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath);
        }
        catch
        {
            // The embedded application icon is cosmetic; startup must not depend on it.
        }

        dataDirectory = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "DeepSeekHarnessDesktop");
        Directory.CreateDirectory(dataDirectory);
        logPath = Path.Combine(dataDirectory, "desktop.log");

        BuildSplash();
        Controls.Add(webView);
        Controls.Add(splash);

        Shown += async (_, _) => await StartOrAttachAsync();
        FormClosing += OnFormClosing;
        KeyDown += OnKeyDown;
    }

    private void BuildSplash()
    {
        var card = new Panel
        {
            BackColor = PanelBackground,
            Size = new Size(480, 330),
            Anchor = AnchorStyles.None
        };

        splash.Controls.Add(card);
        splash.Resize += (_, _) => CenterControl(card, splash);

        var iconBox = new PictureBox
        {
            Size = new Size(86, 86),
            SizeMode = PictureBoxSizeMode.Zoom,
            Location = new Point((card.Width - 86) / 2, 42),
            BackColor = Color.Transparent
        };

        try
        {
            using var icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath);
            iconBox.Image = icon?.ToBitmap();
        }
        catch
        {
            // Keep the splash usable when Windows cannot extract the icon.
        }

        var title = new Label
        {
            Text = "深脉 DeepPulse",
            ForeColor = Color.White,
            Font = new Font("Microsoft YaHei UI", 21f, FontStyle.Bold),
            TextAlign = ContentAlignment.MiddleCenter,
            AutoSize = false,
            Width = card.Width,
            Height = 52,
            Location = new Point(0, 142)
        };

        statusLabel.Location = new Point(20, 196);
        progress.Location = new Point((card.Width - progress.Width) / 2, 251);

        var retryButton = CreateButton("重试", async (_, _) => await StartOrAttachAsync());
        var browserButton = CreateButton("在浏览器中打开", (_, _) => OpenExternal(HarnessUri.AbsoluteUri));
        var logButton = CreateButton("查看日志", (_, _) => OpenExternal(logPath));
        actions.Controls.AddRange([retryButton, browserButton, logButton]);
        actions.PerformLayout();
        actions.Location = new Point((card.Width - actions.PreferredSize.Width) / 2, 279);

        card.Controls.Add(iconBox);
        card.Controls.Add(title);
        card.Controls.Add(statusLabel);
        card.Controls.Add(progress);
        card.Controls.Add(actions);
        CenterControl(card, splash);
    }

    private static Button CreateButton(string text, EventHandler onClick)
    {
        var button = new Button
        {
            Text = text,
            AutoSize = true,
            Height = 34,
            Padding = new Padding(10, 0, 10, 0),
            Margin = new Padding(5, 0, 5, 0),
            FlatStyle = FlatStyle.Flat,
            BackColor = Color.FromArgb(38, 46, 65),
            ForeColor = Color.White,
            Font = new Font("Microsoft YaHei UI", 9f),
            Cursor = Cursors.Hand,
            UseVisualStyleBackColor = false
        };
        button.FlatAppearance.BorderColor = Color.FromArgb(67, 78, 104);
        button.Click += onClick;
        return button;
    }

    private async Task StartOrAttachAsync()
    {
        if (startupInProgress)
        {
            return;
        }

        startupInProgress = true;
        startupCancellation?.Cancel();
        startupCancellation?.Dispose();
        startupCancellation = new CancellationTokenSource();
        var cancellationToken = startupCancellation.Token;

        splash.Visible = true;
        splash.BringToFront();
        webView.Visible = false;
        actions.Visible = false;
        progress.Visible = true;
        statusLabel.ForeColor = MutedText;

        try
        {
            SetStatus("正在检查 DeepSeek Harness…");
            if (!await IsHarnessReadyAsync(cancellationToken))
            {
                installation ??= HarnessInstallation.Find();
                SetStatus("后端未运行，正在自动启动…");
                StartBackend(installation);
                await WaitForBackendAsync(cancellationToken);
            }
            else
            {
                AppendLog("Attached to the existing Harness process on port 3080.");
                SetStatus("已连接，正在准备桌面窗口…");
            }

            SetStatus("正在检查深脉数据服务…");
            if (!await IsDeepPulseReadyAsync(cancellationToken))
            {
                deepPulseInstallation ??= DeepPulseInstallation.Find();
                SetStatus("正在唤醒深脉可信数据服务…");
                StartDeepPulse(deepPulseInstallation);
                await WaitForDeepPulseAsync(cancellationToken);
            }
            else
            {
                AppendLog($"Attached to compatible DeepPulse at {activeDeepPulseBaseUri}.");
            }

            await InitializeWebViewAsync();
            webView.Visible = true;
            webView.BringToFront();
            splash.Visible = false;
            AppendLog("Desktop surface is ready.");
        }
        catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
        {
            // Closing or retrying cancels the current startup attempt.
        }
        catch (Exception ex)
        {
            AppendLog($"Startup failed: {ex}");
            ShowStartupError(ex.Message);
        }
        finally
        {
            startupInProgress = false;
        }
    }

    private void StartBackend(HarnessInstallation foundInstallation)
    {
        if (ownedBackend is { HasExited: false })
        {
            return;
        }

        var startInfo = new ProcessStartInfo
        {
            FileName = foundInstallation.NodeExecutable,
            WorkingDirectory = foundInstallation.ProjectDirectory,
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true
        };
        startInfo.ArgumentList.Add(foundInstallation.CliEntrypoint);
        startInfo.ArgumentList.Add("web");
        startInfo.Environment["PATH"] = string.Join(
            Path.PathSeparator,
            Path.GetDirectoryName(foundInstallation.NodeExecutable),
            Environment.GetEnvironmentVariable("PATH"));
        startInfo.Environment["NO_COLOR"] = "1";

        ownedBackend = new Process { StartInfo = startInfo, EnableRaisingEvents = true };
        ownedBackend.OutputDataReceived += (_, eventArgs) =>
        {
            if (!string.IsNullOrWhiteSpace(eventArgs.Data))
            {
                AppendLog($"[backend] {eventArgs.Data}");
            }
        };
        ownedBackend.ErrorDataReceived += (_, eventArgs) =>
        {
            if (!string.IsNullOrWhiteSpace(eventArgs.Data))
            {
                AppendLog($"[backend:error] {eventArgs.Data}");
            }
        };
        ownedBackend.Exited += (_, _) => AppendLog($"Backend exited with code {ownedBackend?.ExitCode}.");

        if (!ownedBackend.Start())
        {
            throw new InvalidOperationException("无法创建 DeepSeek Harness 后端进程。");
        }

        ownedBackend.BeginOutputReadLine();
        ownedBackend.BeginErrorReadLine();
        AppendLog($"Started backend PID {ownedBackend.Id} using {foundInstallation.CliEntrypoint}.");
    }

    private async Task WaitForBackendAsync(CancellationToken cancellationToken)
    {
        var deadline = DateTimeOffset.UtcNow.AddMinutes(2);
        while (DateTimeOffset.UtcNow < deadline)
        {
            cancellationToken.ThrowIfCancellationRequested();
            if (ownedBackend is { HasExited: true })
            {
                throw new InvalidOperationException(
                    $"DeepSeek Harness 启动进程提前退出（代码 {ownedBackend.ExitCode}）。请查看日志。");
            }

            if (await IsHarnessReadyAsync(cancellationToken))
            {
                SetStatus("后端已就绪，正在打开桌面界面…");
                return;
            }

            await Task.Delay(TimeSpan.FromSeconds(1), cancellationToken);
        }

        throw new TimeoutException("等待 DeepSeek Harness 启动超时。请查看日志后重试。");
    }

    private async Task<bool> IsHarnessReadyAsync(CancellationToken cancellationToken)
    {
        try
        {
            using var response = await httpClient.GetAsync(
                HarnessUri,
                HttpCompletionOption.ResponseHeadersRead,
                cancellationToken);
            return response.StatusCode == HttpStatusCode.OK;
        }
        catch (HttpRequestException)
        {
            return false;
        }
        catch (TaskCanceledException) when (!cancellationToken.IsCancellationRequested)
        {
            return false;
        }
    }

    private void StartDeepPulse(DeepPulseInstallation foundInstallation)
    {
        if (ownedDeepPulse is { HasExited: false })
        {
            return;
        }

        var startInfo = new ProcessStartInfo
        {
            FileName = foundInstallation.PythonExecutable,
            WorkingDirectory = foundInstallation.ProjectDirectory,
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true
        };
        startInfo.ArgumentList.Add("server.py");
        startInfo.Environment["PYTHONUTF8"] = "1";

        ownedDeepPulse = new Process { StartInfo = startInfo, EnableRaisingEvents = true };
        ownedDeepPulse.OutputDataReceived += (_, eventArgs) =>
        {
            if (!string.IsNullOrWhiteSpace(eventArgs.Data))
            {
                AppendLog($"[deeppulse] {eventArgs.Data}");
            }
        };
        ownedDeepPulse.ErrorDataReceived += (_, eventArgs) =>
        {
            if (!string.IsNullOrWhiteSpace(eventArgs.Data))
            {
                AppendLog($"[deeppulse:error] {eventArgs.Data}");
            }
        };
        ownedDeepPulse.Exited += (_, _) =>
            AppendLog($"DeepPulse exited with code {ownedDeepPulse?.ExitCode}.");

        if (!ownedDeepPulse.Start())
        {
            throw new InvalidOperationException("无法创建深脉数据服务进程。");
        }

        ownedDeepPulse.BeginOutputReadLine();
        ownedDeepPulse.BeginErrorReadLine();
        AppendLog($"Started DeepPulse PID {ownedDeepPulse.Id} using {foundInstallation.PythonExecutable}.");
    }

    private async Task WaitForDeepPulseAsync(CancellationToken cancellationToken)
    {
        var deadline = DateTimeOffset.UtcNow.AddSeconds(45);
        while (DateTimeOffset.UtcNow < deadline)
        {
            cancellationToken.ThrowIfCancellationRequested();
            if (ownedDeepPulse is { HasExited: true })
            {
                throw new InvalidOperationException(
                    $"深脉数据服务提前退出（代码 {ownedDeepPulse.ExitCode}）。请查看日志。");
            }

            if (await IsDeepPulseReadyAsync(cancellationToken))
            {
                SetStatus("深脉已就绪，正在打开桌面界面…");
                return;
            }

            await Task.Delay(TimeSpan.FromMilliseconds(750), cancellationToken);
        }

        throw new TimeoutException("等待深脉数据服务启动超时。请查看日志后重试。");
    }

    private async Task<bool> IsDeepPulseReadyAsync(CancellationToken cancellationToken)
    {
        var probes = DeepPulsePorts.Select(port => ProbeDeepPulseAsync(port, cancellationToken));
        var endpoints = await Task.WhenAll(probes);
        activeDeepPulseBaseUri = endpoints.FirstOrDefault(endpoint => endpoint is not null);
        return activeDeepPulseBaseUri is not null;
    }

    private async Task<Uri?> ProbeDeepPulseAsync(int port, CancellationToken cancellationToken)
    {
        var baseUri = new Uri($"http://127.0.0.1:{port}/");
        try
        {
            using var response = await httpClient.GetAsync(
                new Uri(baseUri, "api/health"),
                HttpCompletionOption.ResponseHeadersRead,
                cancellationToken);
            if (response.StatusCode != HttpStatusCode.OK)
            {
                return null;
            }

            await using var stream = await response.Content.ReadAsStreamAsync(cancellationToken);
            using var document = await JsonDocument.ParseAsync(stream, cancellationToken: cancellationToken);
            var root = document.RootElement;
            var health = root.TryGetProperty("data", out var data) && data.ValueKind == JsonValueKind.Object
                ? data
                : root;
            if (!health.TryGetProperty("version", out var rawVersion)
                || !Version.TryParse(rawVersion.GetString(), out var version)
                || version < MinimumDeepPulseVersion)
            {
                return null;
            }

            if (!health.TryGetProperty("capabilities", out var capabilities)
                || !capabilities.TryGetProperty("tdx_read_only", out var readOnly)
                || readOnly.ValueKind != JsonValueKind.True
                || !capabilities.TryGetProperty("proactive_brief", out var proactiveBrief)
                || proactiveBrief.ValueKind != JsonValueKind.Number
                || !proactiveBrief.TryGetInt32(out var proactiveVersion)
                || proactiveVersion != 1
                || !capabilities.TryGetProperty("profile_brief_receipts", out var briefReceipts)
                || briefReceipts.ValueKind != JsonValueKind.Number
                || !briefReceipts.TryGetInt32(out var receiptsVersion)
                || receiptsVersion != 1
                || !capabilities.TryGetProperty("attention_center", out var attentionCenter)
                || attentionCenter.ValueKind != JsonValueKind.Number
                || !attentionCenter.TryGetInt32(out var attentionVersion)
                || attentionVersion != 1
                || !capabilities.TryGetProperty("profile_attention", out var profileAttention)
                || profileAttention.ValueKind != JsonValueKind.Number
                || !profileAttention.TryGetInt32(out var profileAttentionVersion)
                || profileAttentionVersion != 1)
            {
                return null;
            }

            return baseUri;
        }
        catch (HttpRequestException)
        {
            return null;
        }
        catch (JsonException)
        {
            return null;
        }
        catch (TaskCanceledException) when (!cancellationToken.IsCancellationRequested)
        {
            return null;
        }
    }

    private async Task InitializeWebViewAsync()
    {
        if (webView.CoreWebView2 is null)
        {
            var userDataDirectory = Path.Combine(dataDirectory, "WebView2");
            var environment = await CoreWebView2Environment.CreateAsync(
                browserExecutableFolder: null,
                userDataFolder: userDataDirectory);
            await webView.EnsureCoreWebView2Async(environment);

            var core = webView.CoreWebView2
                ?? throw new InvalidOperationException("WebView2 初始化完成后未提供浏览器核心。");
            core.Settings.IsStatusBarEnabled = false;
            core.Settings.AreDevToolsEnabled = false;
            core.Settings.IsZoomControlEnabled = true;
            core.Settings.AreBrowserAcceleratorKeysEnabled = true;
            core.NewWindowRequested += (_, eventArgs) =>
            {
                if (Uri.TryCreate(eventArgs.Uri, UriKind.Absolute, out var target)
                    && target.Host is "127.0.0.1" or "localhost")
                {
                    core.Navigate(target.AbsoluteUri);
                }
                else
                {
                    OpenExternal(eventArgs.Uri);
                }
                eventArgs.Handled = true;
            };
            core.ProcessFailed += (_, eventArgs) =>
            {
                AppendLog($"WebView process failed: {eventArgs.ProcessFailedKind}.");
                BeginInvoke(() => ShowStartupError("桌面渲染进程意外退出，请点击重试。"));
            };
        }

        var navigationCore = webView.CoreWebView2
            ?? throw new InvalidOperationException("WebView2 初始化完成后未提供浏览器核心。");
        if (activeDeepPulseBaseUri is null)
        {
            throw new InvalidOperationException("未找到兼容的深脉 1.7.0+ 数据服务。");
        }
        if (deepPulseBootstrapScriptId is not null)
        {
            navigationCore.RemoveScriptToExecuteOnDocumentCreated(deepPulseBootstrapScriptId);
        }
        var baseUrl = activeDeepPulseBaseUri.GetLeftPart(UriPartial.Authority);
        deepPulseBootstrapScriptId = await navigationCore.AddScriptToExecuteOnDocumentCreatedAsync(
            $"window.__DEEPPULSE_BASE__ = {JsonSerializer.Serialize(baseUrl)};");

        navigationCore.Navigate(HarnessUri.AbsoluteUri);
    }

    private void ShowStartupError(string message)
    {
        webView.Visible = false;
        splash.Visible = true;
        splash.BringToFront();
        progress.Visible = false;
        actions.Visible = true;
        statusLabel.ForeColor = Color.FromArgb(255, 146, 146);
        SetStatus(message);
    }

    private void SetStatus(string message)
    {
        if (InvokeRequired)
        {
            BeginInvoke(() => statusLabel.Text = message);
            return;
        }
        statusLabel.Text = message;
    }

    private void AppendLog(string message)
    {
        try
        {
            lock (logLock)
            {
                File.AppendAllText(
                    logPath,
                    $"{DateTimeOffset.Now:yyyy-MM-dd HH:mm:ss.fff zzz} {message}{Environment.NewLine}");
            }
        }
        catch
        {
            // Logging is diagnostic and must not bring down the desktop surface.
        }
    }

    private static void OpenExternal(string target)
    {
        try
        {
            Process.Start(new ProcessStartInfo(target) { UseShellExecute = true });
        }
        catch (Exception ex)
        {
            MessageBox.Show(ex.Message, "无法打开", MessageBoxButtons.OK, MessageBoxIcon.Warning);
        }
    }

    private void OnKeyDown(object? sender, KeyEventArgs eventArgs)
    {
        if (eventArgs.KeyCode == Keys.F5 || (eventArgs.Control && eventArgs.KeyCode == Keys.R))
        {
            webView.CoreWebView2?.Reload();
            eventArgs.Handled = true;
        }
    }

    private void OnFormClosing(object? sender, FormClosingEventArgs eventArgs)
    {
        startupCancellation?.Cancel();
        StopOwnedProcess(ownedDeepPulse, "DeepPulse");
        StopOwnedProcess(ownedBackend, "backend");
    }

    private void StopOwnedProcess(Process? process, string label)
    {
        if (process is not { HasExited: false })
        {
            return;
        }

        try
        {
            AppendLog($"Stopping owned {label} PID {process.Id}.");
            process.Kill(entireProcessTree: true);
            process.WaitForExit(5_000);
        }
        catch (Exception ex)
        {
            AppendLog($"Failed to stop owned {label}: {ex.Message}");
        }
    }

    private static void CenterControl(Control child, Control parent)
    {
        child.Left = Math.Max(0, (parent.ClientSize.Width - child.Width) / 2);
        child.Top = Math.Max(0, (parent.ClientSize.Height - child.Height) / 2);
    }
}

internal sealed record DeepPulseInstallation(
    string ProjectDirectory,
    string PythonExecutable)
{
    internal static DeepPulseInstallation Find()
    {
        foreach (var candidate in CandidateProjectDirectories())
        {
            var resolved = TryResolve(candidate);
            if (resolved is not null)
            {
                return resolved;
            }
        }

        throw new DirectoryNotFoundException(
            "找不到深脉数据服务。可设置 DEEPPULSE_HOME 指向包含 server.py 的目录。"
            + Environment.NewLine
            + "也可以把 DeepPulse 文件夹放在桌面程序旁边。");
    }

    private static IEnumerable<string> CandidateProjectDirectories()
    {
        var configured = Environment.GetEnvironmentVariable("DEEPPULSE_HOME");
        if (!string.IsNullOrWhiteSpace(configured))
        {
            yield return configured;
        }

        yield return Path.Combine(AppContext.BaseDirectory, "DeepPulse");
        yield return Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory),
            "deepseek身体");
    }

    private static DeepPulseInstallation? TryResolve(string projectDirectory)
    {
        var fullProjectDirectory = Path.GetFullPath(projectDirectory);
        if (!File.Exists(Path.Combine(fullProjectDirectory, "server.py"))
            || !File.Exists(Path.Combine(fullProjectDirectory, "tdx_local.py"))
            || !File.Exists(Path.Combine(fullProjectDirectory, "deeppulse.manifest.json")))
        {
            return null;
        }

        var configuredPython = Environment.GetEnvironmentVariable("DEEPPULSE_PYTHON");
        var pythonCandidates = new[]
        {
            configuredPython,
            Path.Combine(fullProjectDirectory, ".venv", "Scripts", "python.exe"),
            FindOnPath("python.exe"),
            FindOnPath("python3.exe")
        };
        var python = pythonCandidates.FirstOrDefault(path =>
            !string.IsNullOrWhiteSpace(path) && File.Exists(path));
        if (python is null)
        {
            return null;
        }

        return new DeepPulseInstallation(fullProjectDirectory, Path.GetFullPath(python));
    }

    private static string? FindOnPath(string executable)
    {
        var path = Environment.GetEnvironmentVariable("PATH");
        if (string.IsNullOrWhiteSpace(path))
        {
            return null;
        }

        return path.Split(Path.PathSeparator, StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries)
            .Select(directory => Path.Combine(directory.Trim('"'), executable))
            .FirstOrDefault(File.Exists);
    }
}

internal sealed record HarnessInstallation(
    string WorkspaceDirectory,
    string ProjectDirectory,
    string NodeExecutable,
    string CliEntrypoint)
{
    internal static HarnessInstallation Find()
    {
        foreach (var candidate in CandidateWorkspaceDirectories())
        {
            var resolved = TryResolve(candidate);
            if (resolved is not null)
            {
                return resolved;
            }
        }

        throw new DirectoryNotFoundException(
            "找不到已安装的 DeepSeek Harness。可设置 DSH_DESKTOP_HOME 指向包含 deepseek-harness 与 work/runtime 的目录。"
            + Environment.NewLine
            + "也可以先独立启动 Harness 的 3080 端口，再打开本程序。");
    }

    private static IEnumerable<string> CandidateWorkspaceDirectories()
    {
        var configured = Environment.GetEnvironmentVariable("DSH_DESKTOP_HOME");
        if (!string.IsNullOrWhiteSpace(configured))
        {
            yield return configured;
        }

        var directory = new DirectoryInfo(AppContext.BaseDirectory);
        for (var depth = 0; directory is not null && depth < 6; depth++, directory = directory.Parent)
        {
            yield return directory.FullName;
        }

        yield return Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory),
            "DeepSeekHarness");
        yield return Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments),
            "DeepSeekHarness");
    }

    private static HarnessInstallation? TryResolve(string workspaceDirectory)
    {
        var projectDirectory = Path.Combine(workspaceDirectory, "deepseek-harness");
        var cliEntrypoint = Path.Combine(projectDirectory, "apps", "cli", "lib", "bin.js");
        var runtimeRoot = Path.Combine(workspaceDirectory, "work", "runtime");
        if (!File.Exists(cliEntrypoint) || !Directory.Exists(runtimeRoot))
        {
            return null;
        }

        var nodeExecutable = Directory
            .EnumerateFiles(runtimeRoot, "node.exe", SearchOption.AllDirectories)
            .Where(path => path.Contains("node-v", StringComparison.OrdinalIgnoreCase))
            .OrderByDescending(path => path, StringComparer.OrdinalIgnoreCase)
            .FirstOrDefault();
        if (nodeExecutable is null)
        {
            return null;
        }

        return new HarnessInstallation(
            Path.GetFullPath(workspaceDirectory),
            Path.GetFullPath(projectDirectory),
            Path.GetFullPath(nodeExecutable),
            Path.GetFullPath(cliEntrypoint));
    }
}
