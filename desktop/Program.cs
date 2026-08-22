using System.Diagnostics;
using System.ComponentModel;
using System.Net;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.Json;
using Microsoft.Win32.SafeHandles;
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
    private static readonly Version MinimumDeepPulseVersion = new(1, 22, 1);
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
    private readonly NotifyIcon systemNotification = new() { Visible = false };
    private readonly System.Windows.Forms.Timer deliveryTimer = new() { Interval = 30_000 };

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
    private readonly ProcessLifetimeJob? lifetimeJob;
    private bool startupInProgress;
    private bool deliveryPollInProgress;
    private bool ownedDeepPulseProtected;
    private string? lastNotificationItemId;
    private string lastNotificationPage = "overview";
    private string lastNotificationEntityType = "attention";
    private string lastNotificationEntityId = "";
    private string lastNotificationView = "evidence";
    private string lastNotificationTargetFingerprint = "";
    private string lastNotificationRunId = "";

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
            systemNotification.Icon = Icon;
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
        lifetimeJob = ProcessLifetimeJob.TryCreate(out var lifetimeError);
        if (lifetimeJob is null)
        {
            AppendLog($"Process lifetime protection is unavailable: {lifetimeError}");
        }

        BuildSplash();
        Controls.Add(webView);
        Controls.Add(splash);

        Shown += async (_, _) => await StartOrAttachAsync();
        systemNotification.Text = "深脉 DeepPulse";
        systemNotification.BalloonTipClicked += async (_, _) => {
            Show();
            WindowState = FormWindowState.Normal;
            Activate();
            await OpenNotificationTargetAsync();
        };
        deliveryTimer.Tick += async (_, _) => await PollDesktopServicesAsync();
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
            systemNotification.Visible = true;
            deliveryTimer.Start();
            await PollDesktopServicesAsync();
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
        AttachOwnedProcessToLifetimeJob(ownedBackend, "backend");

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
        ownedDeepPulseProtected = AttachOwnedProcessToLifetimeJob(ownedDeepPulse, "DeepPulse");

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
                || profileAttentionVersion != 1
                || !capabilities.TryGetProperty("attention_learning", out var attentionLearning)
                || attentionLearning.ValueKind != JsonValueKind.Number
                || !attentionLearning.TryGetInt32(out var attentionLearningVersion)
                || attentionLearningVersion != 1
                || !capabilities.TryGetProperty("background_monitor", out var backgroundMonitor)
                || backgroundMonitor.ValueKind != JsonValueKind.Number
                || !backgroundMonitor.TryGetInt32(out var backgroundMonitorVersion)
                || backgroundMonitorVersion != 1
                || !capabilities.TryGetProperty("market_routine", out var marketRoutine)
                || marketRoutine.ValueKind != JsonValueKind.Number
                || !marketRoutine.TryGetInt32(out var marketRoutineVersion)
                || marketRoutineVersion != 1
                || !capabilities.TryGetProperty("akshare_enrichment", out var akshareEnrichment)
                || akshareEnrichment.ValueKind != JsonValueKind.Number
                || !akshareEnrichment.TryGetInt32(out var akshareEnrichmentVersion)
                || akshareEnrichmentVersion != 1
                || !capabilities.TryGetProperty("akshare_research_snapshot", out var akshareResearchSnapshot)
                || akshareResearchSnapshot.ValueKind != JsonValueKind.Number
                || !akshareResearchSnapshot.TryGetInt32(out var akshareResearchSnapshotVersion)
                || akshareResearchSnapshotVersion != 1
                || !capabilities.TryGetProperty("akshare_research_packs", out var akshareResearchPacks)
                || akshareResearchPacks.ValueKind != JsonValueKind.Number
                || !akshareResearchPacks.TryGetInt32(out var akshareResearchPacksVersion)
                || akshareResearchPacksVersion != 1
                || !capabilities.TryGetProperty("akshare_interface_health", out var akshareInterfaceHealth)
                || akshareInterfaceHealth.ValueKind != JsonValueKind.Number
                || !akshareInterfaceHealth.TryGetInt32(out var akshareInterfaceHealthVersion)
                || akshareInterfaceHealthVersion != 1
                || !capabilities.TryGetProperty("source_lineage", out var sourceLineage)
                || sourceLineage.ValueKind != JsonValueKind.Number
                || !sourceLineage.TryGetInt32(out var sourceLineageVersion)
                || sourceLineageVersion != 1
                || !capabilities.TryGetProperty("event_impact", out var eventImpact)
                || eventImpact.ValueKind != JsonValueKind.Number
                || !eventImpact.TryGetInt32(out var eventImpactVersion)
                || eventImpactVersion != 2
                || !capabilities.TryGetProperty("event_background_service", out var eventBackgroundService)
                || eventBackgroundService.ValueKind != JsonValueKind.Number
                || !eventBackgroundService.TryGetInt32(out var eventBackgroundServiceVersion)
                || eventBackgroundServiceVersion != 1
                || !capabilities.TryGetProperty("event_relevance_learning", out var eventRelevanceLearning)
                || eventRelevanceLearning.ValueKind != JsonValueKind.Number
                || !eventRelevanceLearning.TryGetInt32(out var eventRelevanceLearningVersion)
                || eventRelevanceLearningVersion != 1
                || !capabilities.TryGetProperty("event_relevance_preview", out var eventRelevancePreview)
                || eventRelevancePreview.ValueKind != JsonValueKind.Number
                || !eventRelevancePreview.TryGetInt32(out var eventRelevancePreviewVersion)
                || eventRelevancePreviewVersion != 1
                || !capabilities.TryGetProperty("event_relevance_delivery_filter", out var eventRelevanceDeliveryFilter)
                || eventRelevanceDeliveryFilter.ValueKind != JsonValueKind.Number
                || !eventRelevanceDeliveryFilter.TryGetInt32(out var eventRelevanceDeliveryFilterVersion)
                || eventRelevanceDeliveryFilterVersion != 1
                || !capabilities.TryGetProperty("research_hypotheses", out var researchHypotheses)
                || researchHypotheses.ValueKind != JsonValueKind.Number
                || !researchHypotheses.TryGetInt32(out var researchHypothesesVersion)
                || researchHypothesesVersion != 1
                || !capabilities.TryGetProperty("hypothesis_due_reminders", out var hypothesisDueReminders)
                || hypothesisDueReminders.ValueKind != JsonValueKind.Number
                || !hypothesisDueReminders.TryGetInt32(out var hypothesisDueRemindersVersion)
                || hypothesisDueRemindersVersion != 1
                || !capabilities.TryGetProperty("hypothesis_evidence_candidates", out var hypothesisEvidenceCandidates)
                || hypothesisEvidenceCandidates.ValueKind != JsonValueKind.Number
                || !hypothesisEvidenceCandidates.TryGetInt32(out var hypothesisEvidenceCandidatesVersion)
                || hypothesisEvidenceCandidatesVersion != 1
                || !capabilities.TryGetProperty("hypothesis_market_control", out var hypothesisMarketControl)
                || hypothesisMarketControl.ValueKind != JsonValueKind.Number
                || !hypothesisMarketControl.TryGetInt32(out var hypothesisMarketControlVersion)
                || hypothesisMarketControlVersion != 1
                || !capabilities.TryGetProperty("unified_delivery", out var unifiedDelivery)
                || unifiedDelivery.ValueKind != JsonValueKind.Number
                || !unifiedDelivery.TryGetInt32(out var unifiedDeliveryVersion)
                || unifiedDeliveryVersion != 1
                || !capabilities.TryGetProperty("desktop_system_notifications", out var desktopNotifications)
                || desktopNotifications.ValueKind != JsonValueKind.Number
                || !desktopNotifications.TryGetInt32(out var desktopNotificationsVersion)
                || desktopNotificationsVersion != 1
                || !capabilities.TryGetProperty("epaper_delivery_receipts", out var epaperReceipts)
                || epaperReceipts.ValueKind != JsonValueKind.Number
                || !epaperReceipts.TryGetInt32(out var epaperReceiptsVersion)
                || epaperReceiptsVersion != 1
                || !capabilities.TryGetProperty("notification_deep_links", out var notificationDeepLinks)
                || notificationDeepLinks.ValueKind != JsonValueKind.Number
                || !notificationDeepLinks.TryGetInt32(out var notificationDeepLinksVersion)
                || notificationDeepLinksVersion != 1
                || !capabilities.TryGetProperty("delivery_timeline", out var deliveryTimeline)
                || deliveryTimeline.ValueKind != JsonValueKind.Number
                || !deliveryTimeline.TryGetInt32(out var deliveryTimelineVersion)
                || deliveryTimelineVersion != 1
                || !capabilities.TryGetProperty("product_diagnostics", out var productDiagnostics)
                || productDiagnostics.ValueKind != JsonValueKind.Number
                || !productDiagnostics.TryGetInt32(out var productDiagnosticsVersion)
                || productDiagnosticsVersion != 1
                || !capabilities.TryGetProperty("diagnostics_export", out var diagnosticsExport)
                || diagnosticsExport.ValueKind != JsonValueKind.Number
                || !diagnosticsExport.TryGetInt32(out var diagnosticsExportVersion)
                || diagnosticsExportVersion != 1
                || !capabilities.TryGetProperty("desktop_heartbeat", out var desktopHeartbeat)
                || desktopHeartbeat.ValueKind != JsonValueKind.Number
                || !desktopHeartbeat.TryGetInt32(out var desktopHeartbeatVersion)
                || desktopHeartbeatVersion != 1
                || !capabilities.TryGetProperty("diagnostic_repairs", out var diagnosticRepairs)
                || diagnosticRepairs.ValueKind != JsonValueKind.Number
                || !diagnosticRepairs.TryGetInt32(out var diagnosticRepairsVersion)
                || diagnosticRepairsVersion != 1
                || !capabilities.TryGetProperty("diagnostic_history", out var diagnosticHistory)
                || diagnosticHistory.ValueKind != JsonValueKind.Number
                || !diagnosticHistory.TryGetInt32(out var diagnosticHistoryVersion)
                || diagnosticHistoryVersion != 1
                || !capabilities.TryGetProperty("diagnostic_issue_template", out var diagnosticIssueTemplate)
                || diagnosticIssueTemplate.ValueKind != JsonValueKind.Number
                || !diagnosticIssueTemplate.TryGetInt32(out var diagnosticIssueTemplateVersion)
                || diagnosticIssueTemplateVersion != 1
                || !capabilities.TryGetProperty("service_plan_preview", out var servicePlanPreview)
                || servicePlanPreview.ValueKind != JsonValueKind.Number
                || !servicePlanPreview.TryGetInt32(out var servicePlanPreviewVersion)
                || servicePlanPreviewVersion != 1
                || !capabilities.TryGetProperty("service_plan_confirm", out var servicePlanConfirm)
                || servicePlanConfirm.ValueKind != JsonValueKind.Number
                || !servicePlanConfirm.TryGetInt32(out var servicePlanConfirmVersion)
                || servicePlanConfirmVersion != 1
                || !capabilities.TryGetProperty("routine_timeline", out var routineTimeline)
                || routineTimeline.ValueKind != JsonValueKind.Number
                || !routineTimeline.TryGetInt32(out var routineTimelineVersion)
                || routineTimelineVersion != 1
                || !capabilities.TryGetProperty("routine_skip_pause", out var routineSkipPause)
                || routineSkipPause.ValueKind != JsonValueKind.Number
                || !routineSkipPause.TryGetInt32(out var routineSkipPauseVersion)
                || routineSkipPauseVersion != 1
                || !capabilities.TryGetProperty("routine_effectiveness", out var routineEffectiveness)
                || routineEffectiveness.ValueKind != JsonValueKind.Number
                || !routineEffectiveness.TryGetInt32(out var routineEffectivenessVersion)
                || routineEffectivenessVersion != 1
                || !capabilities.TryGetProperty("routine_effect_suggestions", out var routineEffectSuggestions)
                || routineEffectSuggestions.ValueKind != JsonValueKind.Number
                || !routineEffectSuggestions.TryGetInt32(out var routineEffectSuggestionsVersion)
                || routineEffectSuggestionsVersion != 1
                || !capabilities.TryGetProperty("routine_effect_undo", out var routineEffectUndo)
                || routineEffectUndo.ValueKind != JsonValueKind.Number
                || !routineEffectUndo.TryGetInt32(out var routineEffectUndoVersion)
                || routineEffectUndoVersion != 1
                || !capabilities.TryGetProperty("routine_trial", out var routineTrial)
                || routineTrial.ValueKind != JsonValueKind.Number
                || !routineTrial.TryGetInt32(out var routineTrialVersion)
                || routineTrialVersion != 1
                || !capabilities.TryGetProperty("routine_trial_confirm", out var routineTrialConfirm)
                || routineTrialConfirm.ValueKind != JsonValueKind.Number
                || !routineTrialConfirm.TryGetInt32(out var routineTrialConfirmVersion)
                || routineTrialConfirmVersion != 1
                || !capabilities.TryGetProperty("routine_authorization_receipts", out var routineAuthorizationReceipts)
                || routineAuthorizationReceipts.ValueKind != JsonValueKind.Number
                || !routineAuthorizationReceipts.TryGetInt32(out var routineAuthorizationReceiptsVersion)
                || routineAuthorizationReceiptsVersion != 1
                || !capabilities.TryGetProperty("research_cockpit", out var researchCockpit)
                || researchCockpit.ValueKind != JsonValueKind.Number
                || !researchCockpit.TryGetInt32(out var researchCockpitVersion)
                || researchCockpitVersion != 1
                || !capabilities.TryGetProperty("research_priority_controls", out var researchPriorityControls)
                || researchPriorityControls.ValueKind != JsonValueKind.Number
                || !researchPriorityControls.TryGetInt32(out var researchPriorityControlsVersion)
                || researchPriorityControlsVersion != 1
                || !capabilities.TryGetProperty("research_cockpit_context", out var researchCockpitContext)
                || researchCockpitContext.ValueKind != JsonValueKind.Number
                || !researchCockpitContext.TryGetInt32(out var researchCockpitContextVersion)
                || researchCockpitContextVersion != 1
                || !capabilities.TryGetProperty("research_memory", out var researchMemory)
                || researchMemory.ValueKind != JsonValueKind.Number
                || !researchMemory.TryGetInt32(out var researchMemoryVersion)
                || researchMemoryVersion != 1
                || !capabilities.TryGetProperty("research_memory_controls", out var researchMemoryControls)
                || researchMemoryControls.ValueKind != JsonValueKind.Number
                || !researchMemoryControls.TryGetInt32(out var researchMemoryControlsVersion)
                || researchMemoryControlsVersion != 1
                || !capabilities.TryGetProperty("research_memory_context", out var researchMemoryContext)
                || researchMemoryContext.ValueKind != JsonValueKind.Number
                || !researchMemoryContext.TryGetInt32(out var researchMemoryContextVersion)
                || researchMemoryContextVersion != 1
                || !capabilities.TryGetProperty("research_workflows", out var researchWorkflows)
                || researchWorkflows.ValueKind != JsonValueKind.Number
                || !researchWorkflows.TryGetInt32(out var researchWorkflowsVersion)
                || researchWorkflowsVersion != 1
                || !capabilities.TryGetProperty("research_workflow_preview", out var researchWorkflowPreview)
                || researchWorkflowPreview.ValueKind != JsonValueKind.Number
                || !researchWorkflowPreview.TryGetInt32(out var researchWorkflowPreviewVersion)
                || researchWorkflowPreviewVersion != 1
                || !capabilities.TryGetProperty("research_workflow_permissions", out var researchWorkflowPermissions)
                || researchWorkflowPermissions.ValueKind != JsonValueKind.Number
                || !researchWorkflowPermissions.TryGetInt32(out var researchWorkflowPermissionsVersion)
                || researchWorkflowPermissionsVersion != 1
                || !capabilities.TryGetProperty("research_result_cards", out var researchResultCards)
                || researchResultCards.ValueKind != JsonValueKind.Number
                || !researchResultCards.TryGetInt32(out var researchResultCardsVersion)
                || researchResultCardsVersion != 1
                || !capabilities.TryGetProperty("research_template_parameters", out var researchTemplateParameters)
                || researchTemplateParameters.ValueKind != JsonValueKind.Number
                || !researchTemplateParameters.TryGetInt32(out var researchTemplateParametersVersion)
                || researchTemplateParametersVersion != 1
                || !capabilities.TryGetProperty("research_run_comparison", out var researchRunComparison)
                || researchRunComparison.ValueKind != JsonValueKind.Number
                || !researchRunComparison.TryGetInt32(out var researchRunComparisonVersion)
                || researchRunComparisonVersion != 1
                || !capabilities.TryGetProperty("research_workflow_lineage", out var researchWorkflowLineage)
                || researchWorkflowLineage.ValueKind != JsonValueKind.Number
                || !researchWorkflowLineage.TryGetInt32(out var researchWorkflowLineageVersion)
                || researchWorkflowLineageVersion != 1
                || !capabilities.TryGetProperty("research_evidence_timeline", out var researchEvidenceTimeline)
                || researchEvidenceTimeline.ValueKind != JsonValueKind.Number
                || !researchEvidenceTimeline.TryGetInt32(out var researchEvidenceTimelineVersion)
                || researchEvidenceTimelineVersion != 1
                || !capabilities.TryGetProperty("research_suggestion_inbox", out var researchSuggestionInbox)
                || researchSuggestionInbox.ValueKind != JsonValueKind.Number
                || !researchSuggestionInbox.TryGetInt32(out var researchSuggestionInboxVersion)
                || researchSuggestionInboxVersion != 1
                || !capabilities.TryGetProperty("research_suggestion_preview", out var researchSuggestionPreview)
                || researchSuggestionPreview.ValueKind != JsonValueKind.Number
                || !researchSuggestionPreview.TryGetInt32(out var researchSuggestionPreviewVersion)
                || researchSuggestionPreviewVersion != 1
                || !capabilities.TryGetProperty("chat_action_plan", out var chatActionPlan)
                || chatActionPlan.ValueKind != JsonValueKind.Number
                || !chatActionPlan.TryGetInt32(out var chatActionPlanVersion)
                || chatActionPlanVersion != 1
                || !capabilities.TryGetProperty("chat_action_receipts", out var chatActionReceipts)
                || chatActionReceipts.ValueKind != JsonValueKind.Number
                 || !chatActionReceipts.TryGetInt32(out var chatActionReceiptsVersion)
                 || chatActionReceiptsVersion != 1
                 || !capabilities.TryGetProperty("proactive_target", out var proactiveTarget)
                 || proactiveTarget.ValueKind != JsonValueKind.Number
                 || !proactiveTarget.TryGetInt32(out var proactiveTargetVersion)
                 || proactiveTargetVersion != 1
                 || !capabilities.TryGetProperty("disposition_receipts", out var dispositionReceipts)
                 || dispositionReceipts.ValueKind != JsonValueKind.Number
                 || !dispositionReceipts.TryGetInt32(out var dispositionReceiptsVersion)
                 || dispositionReceiptsVersion != 1
                 || !capabilities.TryGetProperty("epaper_research_workflow", out var epaperResearchWorkflow)
                || epaperResearchWorkflow.ValueKind != JsonValueKind.Number
                || !epaperResearchWorkflow.TryGetInt32(out var epaperResearchWorkflowVersion)
                || epaperResearchWorkflowVersion != 1)
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
            throw new InvalidOperationException("未找到兼容的深脉 1.39.0+ 数据服务。");
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

    private async Task PollDesktopDeliveryAsync()
    {
        if (deliveryPollInProgress || activeDeepPulseBaseUri is null)
        {
            return;
        }
        deliveryPollInProgress = true;
        string? itemId = null;
        try
        {
            var payload = JsonSerializer.Serialize(new { channel = "desktop", consumer = "windows-app" });
            using var response = await httpClient.PostAsync(
                new Uri(activeDeepPulseBaseUri, "api/delivery/pull"),
                new StringContent(payload, Encoding.UTF8, "application/json"));
            if (!response.IsSuccessStatusCode)
            {
                return;
            }
            await using var stream = await response.Content.ReadAsStreamAsync();
            using var document = await JsonDocument.ParseAsync(stream);
            var root = document.RootElement;
            if (!root.TryGetProperty("data", out var data)
                || !data.TryGetProperty("item", out var item)
                || item.ValueKind != JsonValueKind.Object)
            {
                return;
            }
            itemId = item.TryGetProperty("id", out var rawId) ? rawId.GetString() : null;
            if (string.IsNullOrWhiteSpace(itemId))
            {
                return;
            }
            var title = item.TryGetProperty("title", out var rawTitle)
                ? rawTitle.GetString() : "深脉提醒";
            var detail = item.TryGetProperty("detail", out var rawDetail)
                ? rawDetail.GetString() : "请打开深脉查看详情";
            lastNotificationItemId = itemId;
            lastNotificationPage = item.TryGetProperty("page", out var rawPage)
                ? SanitizePage(rawPage.GetString()) : "overview";
            lastNotificationEntityType = "attention";
            lastNotificationEntityId = itemId;
            lastNotificationView = "evidence";
            lastNotificationTargetFingerprint = "";
            lastNotificationRunId = "";
            if (item.TryGetProperty("attentionGroupId", out var rawGroupId)
                && !string.IsNullOrWhiteSpace(rawGroupId.GetString()))
            {
                lastNotificationItemId = Truncate(rawGroupId.GetString()!, 160);
            }
            if (item.TryGetProperty("target", out var target) && target.ValueKind == JsonValueKind.Object)
            {
                lastNotificationPage = target.TryGetProperty("page", out var targetPage)
                    ? SanitizePage(targetPage.GetString()) : lastNotificationPage;
                lastNotificationEntityType = target.TryGetProperty("entityType", out var entityType)
                    ? Truncate(entityType.GetString() ?? "attention", 40) : "attention";
                lastNotificationEntityId = target.TryGetProperty("entityId", out var entityId)
                    ? Truncate(entityId.GetString() ?? "", 180) : "";
                lastNotificationView = target.TryGetProperty("view", out var targetView)
                    ? Truncate(targetView.GetString() ?? "evidence", 40) : "evidence";
                lastNotificationTargetFingerprint = target.TryGetProperty("fingerprint", out var fingerprint)
                    ? Truncate(fingerprint.GetString() ?? "", 80) : "";
                lastNotificationRunId = target.TryGetProperty("runId", out var runId)
                    ? Truncate(runId.GetString() ?? "", 180) : "";
            }
            systemNotification.BalloonTipTitle = Truncate(title ?? "深脉提醒", 63);
            systemNotification.BalloonTipText = Truncate(detail ?? "请打开深脉查看详情", 240);
            systemNotification.BalloonTipIcon = item.TryGetProperty("priority", out var priority)
                && priority.GetString() == "high" ? ToolTipIcon.Warning : ToolTipIcon.Info;
            systemNotification.ShowBalloonTip(9000);
            await AcknowledgeDesktopDeliveryAsync(itemId, "delivered", "");
        }
        catch (Exception ex)
        {
            AppendLog($"Desktop notification poll failed: {ex.Message}");
            if (!string.IsNullOrWhiteSpace(itemId))
            {
                await AcknowledgeDesktopDeliveryAsync(itemId, "failed", ex.Message);
            }
        }
        finally
        {
            deliveryPollInProgress = false;
        }
    }

    private async Task PollDesktopServicesAsync()
    {
        await SendDesktopHeartbeatAsync();
        await PollDesktopDeliveryAsync();
    }

    private async Task SendDesktopHeartbeatAsync()
    {
        if (activeDeepPulseBaseUri is null) return;
        try
        {
            var assemblyVersion = typeof(HarnessForm).Assembly.GetName().Version?.ToString(3) ?? "unknown";
            var productVersion = FileVersionInfo.GetVersionInfo(Application.ExecutablePath).ProductVersion
                ?? assemblyVersion;
            var serviceOwnership = ownedDeepPulse is { HasExited: false } ? "owned" : "attached";
            var payload = JsonSerializer.Serialize(new {
                appVersion = assemblyVersion,
                productVersion,
                surface = "windows-desktop",
                serviceOwnership,
                processLifetimeProtected = serviceOwnership == "owned" && ownedDeepPulseProtected
            });
            using var response = await httpClient.PostAsync(
                new Uri(activeDeepPulseBaseUri, "api/diagnostics/desktop-heartbeat"),
                new StringContent(payload, Encoding.UTF8, "application/json"));
            if (!response.IsSuccessStatusCode)
            {
                AppendLog($"Desktop heartbeat returned HTTP {(int)response.StatusCode}.");
            }
        }
        catch (Exception ex)
        {
            AppendLog($"Desktop heartbeat failed: {ex.Message}");
        }
    }

    private async Task AcknowledgeDesktopDeliveryAsync(string itemId, string status, string error)
    {
        if (activeDeepPulseBaseUri is null) return;
        try
        {
            var payload = JsonSerializer.Serialize(new {
                channel = "desktop", itemId, status, consumer = "windows-app", error
            });
            using var response = await httpClient.PostAsync(
                new Uri(activeDeepPulseBaseUri, "api/delivery/ack"),
                new StringContent(payload, Encoding.UTF8, "application/json"));
            if (!response.IsSuccessStatusCode)
            {
                AppendLog($"Desktop notification acknowledgement returned HTTP {(int)response.StatusCode}.");
            }
        }
        catch (Exception ex)
        {
            AppendLog($"Desktop notification acknowledgement failed: {ex.Message}");
        }
    }

    private async Task OpenNotificationTargetAsync()
    {
        if (webView.CoreWebView2 is null || string.IsNullOrWhiteSpace(lastNotificationItemId))
        {
            return;
        }
        var parameters = new List<string> {
            $"page={Uri.EscapeDataString(lastNotificationPage)}",
            $"entityType={Uri.EscapeDataString(lastNotificationEntityType)}",
            $"entityId={Uri.EscapeDataString(lastNotificationEntityId)}",
            $"view={Uri.EscapeDataString(lastNotificationView)}",
            $"fingerprint={Uri.EscapeDataString(lastNotificationTargetFingerprint)}"
        };
        if (!string.IsNullOrWhiteSpace(lastNotificationRunId))
        {
            parameters.Add($"runId={Uri.EscapeDataString(lastNotificationRunId)}");
        }
        var target = $"deeppulse://attention/{Uri.EscapeDataString(lastNotificationItemId)}?"
            + string.Join("&", parameters);
        var script = "(() => { const link = document.createElement('a');"
            + $"link.setAttribute('href', {JsonSerializer.Serialize(target)});"
            + "link.style.display='none';document.body.appendChild(link);link.click();link.remove(); })();";
        try
        {
            await webView.CoreWebView2.ExecuteScriptAsync(script);
        }
        catch (Exception ex)
        {
            AppendLog($"Notification deep link failed: {ex.Message}");
        }
    }

    private static string SanitizePage(string? value)
    {
        var allowed = new HashSet<string>(StringComparer.OrdinalIgnoreCase) {
            "overview", "emotion", "market", "ladder", "watch",
            "strategy", "epaper", "datasrc", "about"
        };
        return value is not null && allowed.Contains(value) ? value.ToLowerInvariant() : "overview";
    }

    private static string Truncate(string value, int maximum) =>
        value.Length <= maximum ? value : value[..Math.Max(1, maximum - 1)] + "…";

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
        deliveryTimer.Stop();
        systemNotification.Visible = false;
        systemNotification.Dispose();
        StopOwnedProcess(ownedDeepPulse, "DeepPulse");
        StopOwnedProcess(ownedBackend, "backend");
        lifetimeJob?.Dispose();
    }

    private bool AttachOwnedProcessToLifetimeJob(Process process, string label)
    {
        if (lifetimeJob is null)
        {
            return false;
        }
        if (!lifetimeJob.TryAdd(process, out var error))
        {
            AppendLog($"Could not protect owned {label} PID {process.Id}: {error}");
            return false;
        }
        AppendLog($"Protected owned {label} PID {process.Id} with kill-on-close lifetime job.");
        return true;
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

internal sealed class ProcessLifetimeJob : IDisposable
{
    private const uint JobObjectLimitKillOnJobClose = 0x00002000;
    private const int JobObjectExtendedLimitInformationClass = 9;
    private readonly SafeFileHandle handle;

    private ProcessLifetimeJob(SafeFileHandle handle)
    {
        this.handle = handle;
    }

    internal static ProcessLifetimeJob? TryCreate(out string error)
    {
        error = "";
        SafeFileHandle? job = null;
        IntPtr buffer = IntPtr.Zero;
        try
        {
            job = CreateJobObject(IntPtr.Zero, null);
            if (job.IsInvalid)
            {
                throw new Win32Exception(Marshal.GetLastWin32Error());
            }

            var limits = new JobObjectExtendedLimitInformation
            {
                BasicLimitInformation = new JobObjectBasicLimitInformation
                {
                    LimitFlags = JobObjectLimitKillOnJobClose
                }
            };
            var size = Marshal.SizeOf<JobObjectExtendedLimitInformation>();
            buffer = Marshal.AllocHGlobal(size);
            Marshal.StructureToPtr(limits, buffer, false);
            if (!SetInformationJobObject(job, JobObjectExtendedLimitInformationClass, buffer, (uint)size))
            {
                throw new Win32Exception(Marshal.GetLastWin32Error());
            }
            return new ProcessLifetimeJob(job);
        }
        catch (Exception ex)
        {
            job?.Dispose();
            error = ex.Message;
            return null;
        }
        finally
        {
            if (buffer != IntPtr.Zero)
            {
                Marshal.FreeHGlobal(buffer);
            }
        }
    }

    internal bool TryAdd(Process process, out string error)
    {
        error = "";
        if (handle.IsInvalid || handle.IsClosed)
        {
            error = "lifetime job is closed";
            return false;
        }
        if (AssignProcessToJobObject(handle, process.Handle))
        {
            return true;
        }
        error = new Win32Exception(Marshal.GetLastWin32Error()).Message;
        return false;
    }

    public void Dispose() => handle.Dispose();

    [StructLayout(LayoutKind.Sequential)]
    private struct JobObjectBasicLimitInformation
    {
        public long PerProcessUserTimeLimit;
        public long PerJobUserTimeLimit;
        public uint LimitFlags;
        public UIntPtr MinimumWorkingSetSize;
        public UIntPtr MaximumWorkingSetSize;
        public uint ActiveProcessLimit;
        public UIntPtr Affinity;
        public uint PriorityClass;
        public uint SchedulingClass;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct IoCounters
    {
        public ulong ReadOperationCount;
        public ulong WriteOperationCount;
        public ulong OtherOperationCount;
        public ulong ReadTransferCount;
        public ulong WriteTransferCount;
        public ulong OtherTransferCount;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct JobObjectExtendedLimitInformation
    {
        public JobObjectBasicLimitInformation BasicLimitInformation;
        public IoCounters IoInfo;
        public UIntPtr ProcessMemoryLimit;
        public UIntPtr JobMemoryLimit;
        public UIntPtr PeakProcessMemoryUsed;
        public UIntPtr PeakJobMemoryUsed;
    }

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern SafeFileHandle CreateJobObject(IntPtr jobAttributes, string? name);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool SetInformationJobObject(
        SafeFileHandle job,
        int informationClass,
        IntPtr information,
        uint informationLength);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool AssignProcessToJobObject(SafeFileHandle job, IntPtr process);
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
