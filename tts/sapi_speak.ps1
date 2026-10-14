# Offline-Fallback-Sprachausgabe ueber Windows SAPI (deutsche Stimme "Hedda").
# Aufruf: powershell -File sapi_speak.ps1 <textdatei> <wav-ausgabe> <json-ausgabe>
param([string]$TextFile, [string]$WavOut, [string]$JsonOut)
Add-Type -AssemblyName System.Speech
$text = [System.IO.File]::ReadAllText($TextFile, [System.Text.Encoding]::UTF8)
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
$voice = $synth.GetInstalledVoices() | Where-Object { $_.VoiceInfo.Culture.Name -eq 'de-DE' } | Select-Object -First 1
if (-not $voice) { Write-Error 'Keine deutsche SAPI-Stimme installiert'; exit 2 }
$synth.SelectVoice($voice.VoiceInfo.Name)
$synth.Rate = 1
$synth.SetOutputToWaveFile($WavOut)
$script:words = New-Object System.Collections.ArrayList
$synth.add_SpeakProgress({
    param($sender, $e)
    [void]$script:words.Add(@{ text = $e.Text; start = $e.AudioPosition.TotalSeconds })
})
$synth.Speak($text)
$synth.SetOutputToNull()
$synth.Dispose()
$json = ConvertTo-Json -InputObject @($script:words) -Compress
[System.IO.File]::WriteAllText($JsonOut, $json, (New-Object System.Text.UTF8Encoding($false)))
exit 0
