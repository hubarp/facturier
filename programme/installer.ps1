# Installation du Facturier (une seule fois) : Python si besoin, puis raccourcis « Facturier » avec icône.
$ErrorActionPreference = 'Stop'
$prog = $PSScriptRoot
$racine = Split-Path -Parent $prog
$script = Join-Path $prog 'facturier.pyw'

function Tester-Python($pythonExe, $argsPy) {
    try {
        $sortie = & $pythonExe @argsPy -c "import sys; print(sys.version_info >= (3, 9))" 2>$null
        return ($sortie -match 'True')
    } catch { return $false }
}

# Renvoie @{ exe = programme sans console ; args = arguments } ou $null
function Trouver-Python {
    $lanceurs = @((Get-Command py.exe -ErrorAction SilentlyContinue).Source, "$env:LOCALAPPDATA\Programs\Python\Launcher\py.exe", "$env:WINDIR\py.exe") | Where-Object { $_ -and (Test-Path $_) }
    foreach ($py in $lanceurs) {
        $pyw = Join-Path (Split-Path $py) 'pyw.exe'
        if ((Test-Path $pyw) -and (Tester-Python $py @('-3'))) { return @{ exe = $pyw; args = '-3' } }
    }
    $dossiers = @(Get-ChildItem "$env:LOCALAPPDATA\Programs\Python\Python3*", "$env:ProgramFiles\Python3*" -Directory -ErrorAction SilentlyContinue | Sort-Object Name -Descending)
    $cmd = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($cmd) { $dossiers += Get-Item (Split-Path $cmd.Source) }
    foreach ($d in $dossiers) {
        $python = Join-Path $d.FullName 'python.exe'; $pythonw = Join-Path $d.FullName 'pythonw.exe'
        if ((Test-Path $pythonw) -and (Tester-Python $python @())) { return @{ exe = $pythonw; args = '' } }
    }
    return $null
}

Write-Host ''
Write-Host '=== Installation du Facturier ===' -ForegroundColor Cyan
$py = Trouver-Python
if (-not $py) {
    Write-Host 'Python n''est pas installé : installation en cours (quelques minutes)...'
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        winget install -e --id Python.Python.3.12 --scope user --silent --accept-package-agreements --accept-source-agreements | Out-Host
        $py = Trouver-Python
    }
}
if (-not $py) {
    Write-Host ''
    Write-Host 'Python n''a pas pu être installé automatiquement.' -ForegroundColor Yellow
    Write-Host 'La page de téléchargement va s''ouvrir : installez Python (bouton jaune « Download Python »),'
    Write-Host 'en cochant « Add python.exe to PATH », puis relancez « Installer le Facturier ».'
    Start-Process 'https://www.python.org/downloads/windows/'
    exit 1
}
Write-Host "Python trouvé : $($py.exe)"

$shell = New-Object -ComObject WScript.Shell
$cibles = @(
    (Join-Path ([Environment]::GetFolderPath('Desktop')) 'Facturier.lnk'),
    (Join-Path ([Environment]::GetFolderPath('Programs')) 'Facturier.lnk'),
    (Join-Path $racine 'Facturier.lnk')
)
foreach ($c in $cibles) {
    $l = $shell.CreateShortcut($c)
    $l.TargetPath = $py.exe
    $l.Arguments = ("$($py.args) -I `"$script`"").Trim()      # -I : aucun module pris dans le dossier du programme
    $l.WorkingDirectory = $prog
    $l.IconLocation = (Join-Path $prog 'facturier.ico') + ',0'
    $l.Description = 'Facturier'
    $l.Save()
}
Write-Host 'Raccourci « Facturier » créé sur le bureau, dans le menu Démarrer et dans ce dossier.' -ForegroundColor Green
Write-Host 'Ouverture du Facturier...'
Start-Process -FilePath $py.exe -ArgumentList (("$($py.args) -I `"$script`"").Trim()) -WorkingDirectory $prog
Write-Host ''
Write-Host 'Terminé. Désormais, ouvrez le Facturier par son icône.' -ForegroundColor Green
