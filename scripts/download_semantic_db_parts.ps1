[CmdletBinding()]
param(
    [string]$Destination = (Join-Path $env:TEMP 'mirae-semantic-v1-parts'),
    [ValidateRange(1, 12)]
    [int]$Parallel = 6
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$parts = @(
    [pscustomobject]@{ Name = 'disclosure_corpus_semantic_v1.sqlite.zst.part000'; Id = '1AXpbIS2ACco3DvkhnpdN3Y7yiB8iy8FC'; Size = 99614720L }
    [pscustomobject]@{ Name = 'disclosure_corpus_semantic_v1.sqlite.zst.part001'; Id = '11R-mbuiHvluX_-HH8Ru_qwB9tcxMVW9N'; Size = 99614720L }
    [pscustomobject]@{ Name = 'disclosure_corpus_semantic_v1.sqlite.zst.part002'; Id = '1bY3KzmkjHK66-CCiPQu897Fpg1LDz3BU'; Size = 99614720L }
    [pscustomobject]@{ Name = 'disclosure_corpus_semantic_v1.sqlite.zst.part003'; Id = '12X8mq7ggIF_TWc6cS0QkPub9nFzqaNmG'; Size = 99614720L }
    [pscustomobject]@{ Name = 'disclosure_corpus_semantic_v1.sqlite.zst.part004'; Id = '1U6hG_LiUaHBvUKP-ZpDBUHWKCW-Tuh4d'; Size = 99614720L }
    [pscustomobject]@{ Name = 'disclosure_corpus_semantic_v1.sqlite.zst.part005'; Id = '1NtKO-e0u-AHaq1FeJBXwNmidKlW6Q7re'; Size = 99614720L }
    [pscustomobject]@{ Name = 'disclosure_corpus_semantic_v1.sqlite.zst.part006'; Id = '1Qqt69Kcr1Mu_1JxVORcGVcj1KTN_gJvw'; Size = 99614720L }
    [pscustomobject]@{ Name = 'disclosure_corpus_semantic_v1.sqlite.zst.part007'; Id = '10kBGDkjpZ8ow8dA_cdossZbNki8OQhQT'; Size = 99614720L }
    [pscustomobject]@{ Name = 'disclosure_corpus_semantic_v1.sqlite.zst.part008'; Id = '1G-H_9sOJ3eZ_VsD2f1un2BTq7PxONYHe'; Size = 99614720L }
    [pscustomobject]@{ Name = 'disclosure_corpus_semantic_v1.sqlite.zst.part009'; Id = '1qewrKaQx6e01OWeIeLsQWo3PUx3q2_iY'; Size = 99614720L }
    [pscustomobject]@{ Name = 'disclosure_corpus_semantic_v1.sqlite.zst.part010'; Id = '1qx-wAON4-lCPDevFcvoLugjtukWsGesr'; Size = 99614720L }
    [pscustomobject]@{ Name = 'disclosure_corpus_semantic_v1.sqlite.zst.part011'; Id = '13LsmTXGiB7GdjTg9ptIgSKrNs2R1T3Rn'; Size = 99614720L }
    [pscustomobject]@{ Name = 'disclosure_corpus_semantic_v1.sqlite.zst.part012'; Id = '1bEOhfRYFUSxv65fApYaWfWcIt-xWnmXr'; Size = 99614720L }
    [pscustomobject]@{ Name = 'disclosure_corpus_semantic_v1.sqlite.zst.part013'; Id = '1AMgQxIORuVjXnMv13OjjyfI3TJzu9Ox8'; Size = 99614720L }
    [pscustomobject]@{ Name = 'disclosure_corpus_semantic_v1.sqlite.zst.part014'; Id = '1-oLVuVWzz_UDIPBhkv3egfyDfR78Zjjk'; Size = 99614720L }
    [pscustomobject]@{ Name = 'disclosure_corpus_semantic_v1.sqlite.zst.part015'; Id = '1WHhsgmZUqyWOtG7nrYFOu9iH0tJmA7_A'; Size = 99614720L }
    [pscustomobject]@{ Name = 'disclosure_corpus_semantic_v1.sqlite.zst.part016'; Id = '1NwysJOjt1N_7mj4_3eCLyQSLTQ1wIaLg'; Size = 99614720L }
    [pscustomobject]@{ Name = 'disclosure_corpus_semantic_v1.sqlite.zst.part017'; Id = '1s5W-9doUxyiaA65FJpNrDNr1IylUFeOh'; Size = 99614720L }
    [pscustomobject]@{ Name = 'disclosure_corpus_semantic_v1.sqlite.zst.part018'; Id = '1_N4AZ2jmNB2epPNyMXbF0-Yi5KyYs-dO'; Size = 99614720L }
    [pscustomobject]@{ Name = 'disclosure_corpus_semantic_v1.sqlite.zst.part019'; Id = '1-BEodN-fsPsCbq56fxFzkMJE-qirhY1A'; Size = 99614720L }
    [pscustomobject]@{ Name = 'disclosure_corpus_semantic_v1.sqlite.zst.part020'; Id = '1U8jJkMu-mJuxrKmrTMXmw3V-2iCM_TbY'; Size = 99614720L }
    [pscustomobject]@{ Name = 'disclosure_corpus_semantic_v1.sqlite.zst.part021'; Id = '1oIH_CbCOIxn-GByAJbDlr6mvwbvoKSg2'; Size = 99614720L }
    [pscustomobject]@{ Name = 'disclosure_corpus_semantic_v1.sqlite.zst.part022'; Id = '1MTu6IbEKOo4eEmgUf_Af2WgCePjXwN0i'; Size = 99614720L }
    [pscustomobject]@{ Name = 'disclosure_corpus_semantic_v1.sqlite.zst.part023'; Id = '18nSssJBrzL-oTDPxRfvUFK7weNUCf56v'; Size = 99614720L }
    [pscustomobject]@{ Name = 'disclosure_corpus_semantic_v1.sqlite.zst.part024'; Id = '1k6cMTWKtBkdjCRLto-iPYBW4h_zVriXk'; Size = 99614720L }
    [pscustomobject]@{ Name = 'disclosure_corpus_semantic_v1.sqlite.zst.part025'; Id = '1inCiEfrLpvS6XmIIfbKQ63ti0Lv1bsjR'; Size = 99614720L }
    [pscustomobject]@{ Name = 'disclosure_corpus_semantic_v1.sqlite.zst.part026'; Id = '1uR0YPffj70QBuopSiNmCJTairs3E_8US'; Size = 99614720L }
    [pscustomobject]@{ Name = 'disclosure_corpus_semantic_v1.sqlite.zst.part027'; Id = '1Iabe2Fia6WX6qlqV_4tWGyQo8SW7FSUu'; Size = 99614720L }
    [pscustomobject]@{ Name = 'disclosure_corpus_semantic_v1.sqlite.zst.part028'; Id = '1M1bB3Gw5rUVPokTmoqTjo7tPf4LaTOvl'; Size = 99614720L }
    [pscustomobject]@{ Name = 'disclosure_corpus_semantic_v1.sqlite.zst.part029'; Id = '17TOFrY302hWkJXouNDazGoaSuNh7hUsy'; Size = 99614720L }
    [pscustomobject]@{ Name = 'disclosure_corpus_semantic_v1.sqlite.zst.part030'; Id = '1ltS9HfomhQECH2WNrt0anbjxuCfJO5ug'; Size = 99614720L }
    [pscustomobject]@{ Name = 'disclosure_corpus_semantic_v1.sqlite.zst.part031'; Id = '1bRJWIUFqrtJPri9RTud5o0F9W0xBehU_'; Size = 99614720L }
    [pscustomobject]@{ Name = 'disclosure_corpus_semantic_v1.sqlite.zst.part032'; Id = '1aszdE-LGH1Ie2NOsNZtrxToGlm5OI0W4'; Size = 99614720L }
    [pscustomobject]@{ Name = 'disclosure_corpus_semantic_v1.sqlite.zst.part033'; Id = '1g4F4gghDFlsWnnVJjhow-eN1UcBTHUHC'; Size = 99614720L }
    [pscustomobject]@{ Name = 'disclosure_corpus_semantic_v1.sqlite.zst.part034'; Id = '1BfozvC8efyC1EHGbhYYlz-e4qZpPsvP3'; Size = 99614720L }
    [pscustomobject]@{ Name = 'disclosure_corpus_semantic_v1.sqlite.zst.part035'; Id = '1xfIU_wkoYzqjj1CekFz38Z57_nfuOZqt'; Size = 99614720L }
    [pscustomobject]@{ Name = 'disclosure_corpus_semantic_v1.sqlite.zst.part036'; Id = '15uQepQIGYjZmqX5jF85SegslB5cf6pGS'; Size = 99614720L }
    [pscustomobject]@{ Name = 'disclosure_corpus_semantic_v1.sqlite.zst.part037'; Id = '1bxJtfr0Zy6R_dPwZhkZOoQ_wSL-foDZx'; Size = 99614720L }
    [pscustomobject]@{ Name = 'disclosure_corpus_semantic_v1.sqlite.zst.part038'; Id = '1v3Ozb4N91V3xr4HMhcbCaWSbhHXteJSm'; Size = 99614720L }
    [pscustomobject]@{ Name = 'disclosure_corpus_semantic_v1.sqlite.zst.part039'; Id = '1TJ7oU8s2JSiQMd6balPOD424InB0AqWg'; Size = 99614720L }
    [pscustomobject]@{ Name = 'disclosure_corpus_semantic_v1.sqlite.zst.part040'; Id = '1OYOnJuvIw5zBMnDm1XwHlQFlU0IhwSf6'; Size = 99614720L }
    [pscustomobject]@{ Name = 'disclosure_corpus_semantic_v1.sqlite.zst.part041'; Id = '1RSF-kiCJNOENH7JftiOW2kx0BHWcLRzF'; Size = 99614720L }
    [pscustomobject]@{ Name = 'disclosure_corpus_semantic_v1.sqlite.zst.part042'; Id = '14v7hvjhim53k6y8sRnNCjWhanjzQLQ1x'; Size = 99614720L }
    [pscustomobject]@{ Name = 'disclosure_corpus_semantic_v1.sqlite.zst.part043'; Id = '18rxFkqnUiOx_-Kmue1cokSq23iyjj-JH'; Size = 99614720L }
    [pscustomobject]@{ Name = 'disclosure_corpus_semantic_v1.sqlite.zst.part044'; Id = '1IFGN4-vCkjmVxGZXx8Su9rCOFxnneMH4'; Size = 99614720L }
    [pscustomobject]@{ Name = 'disclosure_corpus_semantic_v1.sqlite.zst.part045'; Id = '1zfM7KEPiU71XcpaAJGFTDxHoe-NeIFiC'; Size = 99614720L }
    [pscustomobject]@{ Name = 'disclosure_corpus_semantic_v1.sqlite.zst.part046'; Id = '1q-bXT-AHrd13z94XFydZnb-QbYUlvXIP'; Size = 99614720L }
    [pscustomobject]@{ Name = 'disclosure_corpus_semantic_v1.sqlite.zst.part047'; Id = '12gJSgzvmM7e3ogsjGscyXE7tLwIieDV9'; Size = 99614720L }
    [pscustomobject]@{ Name = 'disclosure_corpus_semantic_v1.sqlite.zst.part048'; Id = '1Z8kEZ2AHQYocedzkJcnyIUkM9pEU8s_x'; Size = 99614720L }
    [pscustomobject]@{ Name = 'disclosure_corpus_semantic_v1.sqlite.zst.part049'; Id = '1sZSiRdTow-P06mzRO74FSZ4FwA8iAtXN'; Size = 99614720L }
    [pscustomobject]@{ Name = 'disclosure_corpus_semantic_v1.sqlite.zst.part050'; Id = '1zcaTsN0kZ4nD69UoRs-nr7z63vRM-rPd'; Size = 99614720L }
    [pscustomobject]@{ Name = 'disclosure_corpus_semantic_v1.sqlite.zst.part051'; Id = '1JXgVnUnygHl_GQEvZLJyk5-t-GOjtVry'; Size = 99614720L }
    [pscustomobject]@{ Name = 'disclosure_corpus_semantic_v1.sqlite.zst.part052'; Id = '1cZuvzTkOSRk5EBJxFtRK0WAwrL5j3_YG'; Size = 99614720L }
    [pscustomobject]@{ Name = 'disclosure_corpus_semantic_v1.sqlite.zst.part053'; Id = '1Z2dkCRwcOEX4odYoHFQ3MI4hgwGN4x1D'; Size = 99614720L }
    [pscustomobject]@{ Name = 'disclosure_corpus_semantic_v1.sqlite.zst.part054'; Id = '1nXyA-yDdW5EsIloeKj6gmEg1gpFbhOQe'; Size = 99614720L }
    [pscustomobject]@{ Name = 'disclosure_corpus_semantic_v1.sqlite.zst.part055'; Id = '1D0MJJwtl-8AXDeuu6dstoHjXiMUmY53F'; Size = 99614720L }
    [pscustomobject]@{ Name = 'disclosure_corpus_semantic_v1.sqlite.zst.part056'; Id = '1s6OHlAk7ZRr9oKRBDB3eg3UTzkkopWln'; Size = 99614720L }
    [pscustomobject]@{ Name = 'disclosure_corpus_semantic_v1.sqlite.zst.part057'; Id = '15ivQZt45jo8K20CpIJljxwYhBvQQMgjZ'; Size = 99614720L }
    [pscustomobject]@{ Name = 'disclosure_corpus_semantic_v1.sqlite.zst.part058'; Id = '16S398fvlUY0ZpbKto_d6d-ExzVqJs7cc'; Size = 99614720L }
    [pscustomobject]@{ Name = 'disclosure_corpus_semantic_v1.sqlite.zst.part059'; Id = '1_fKvq3-he6jnWtZFsSTA2nayTC35pIt5'; Size = 99614720L }
    [pscustomobject]@{ Name = 'disclosure_corpus_semantic_v1.sqlite.zst.part060'; Id = '1Ms51UixmxJB86nHOf8Zmdl6Z1Pr0Zun5'; Size = 99614720L }
    [pscustomobject]@{ Name = 'disclosure_corpus_semantic_v1.sqlite.zst.part061'; Id = '1Rm6BzD798ptWsWG1DXZ1uk3FrQ-HGhrg'; Size = 94554204L }
)

$destinationPath = [System.IO.Path]::GetFullPath($Destination)
[System.IO.Directory]::CreateDirectory($destinationPath) | Out-Null

$parts | ForEach-Object -Parallel {
    $target = Join-Path $using:destinationPath $_.Name
    $partial = "$target.partial"

    if ((Test-Path -LiteralPath $target) -and (Get-Item -LiteralPath $target).Length -eq $_.Size) {
        return
    }

    if (Test-Path -LiteralPath $partial) {
        Remove-Item -LiteralPath $partial -Force
    }

    $url = "https://drive.usercontent.google.com/download?id=$($_.Id)&export=download&confirm=t"
    & curl.exe -L --fail --silent --show-error --retry 5 --retry-all-errors --connect-timeout 20 $url -o $partial
    if ($LASTEXITCODE -ne 0) {
        throw "Download failed: $($_.Name)"
    }

    $actual = (Get-Item -LiteralPath $partial).Length
    if ($actual -ne $_.Size) {
        throw "Size mismatch for $($_.Name): expected $($_.Size), got $actual"
    }

    Move-Item -LiteralPath $partial -Destination $target
} -ThrottleLimit $Parallel

$downloaded = Get-ChildItem -LiteralPath $destinationPath -File |
    Where-Object Name -Match '^disclosure_corpus_semantic_v1\.sqlite\.zst\.part\d{3}$' |
    Sort-Object Name

if ($downloaded.Count -ne 62) {
    throw "Expected 62 downloaded parts, found $($downloaded.Count)"
}

$totalBytes = ($downloaded | Measure-Object Length -Sum).Sum
if ($totalBytes -ne 6171052124) {
    throw "Total size mismatch: expected 6171052124, got $totalBytes"
}

[pscustomobject]@{
    Directory = $destinationPath
    PartCount = $downloaded.Count
    TotalBytes = $totalBytes
}
