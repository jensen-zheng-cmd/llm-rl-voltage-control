param(
    [string]$Template='D:\Codex\work\llm-rl-ppt-references-20261113\references-draft.pptx',
    [string]$Paper='D:\obsidian_save\论文库\Two-Stage Active Distribution Network Vo\zh-Two-Stage Active Distribution Network Vo.md',
    [string]$BuildDir='D:\Codex\work\llm-rl-ppt-rl-literature-20261113'
)
$ErrorActionPreference='Stop'
New-Item -ItemType Directory -Path $BuildDir -Force|Out-Null
$draft=Join-Path $BuildDir 'rl-literature-draft.pptx'
Copy-Item -LiteralPath $Template -Destination $draft -Force
$refs=@{}
foreach($line in Get-Content -LiteralPath $Paper -Encoding UTF8){
    if($line -match '^\[(5|6|7|8|9|10|11|12|13|14)\] '){$refs[[string]$Matches[1]]=($line -replace '\s+\^rc-\d+\s*$','')}
}
if($refs.Count -ne 10){throw 'Expected complete bibliography entries 5–14.'}
$purple=104+256*11+65536*122
function AddText($slide,$name,$value,[double]$x,[double]$y,[double]$w,[double]$h,[double]$size,$font,[int]$color,[bool]$bold=$false){
    $s=$slide.Shapes.AddTextbox(1,$x,$y,$w,$h);$s.Name=$name
    $s.TextFrame.MarginLeft=0;$s.TextFrame.MarginRight=0;$s.TextFrame.MarginTop=0;$s.TextFrame.MarginBottom=0
    $s.TextFrame.AutoSize=0;$s.TextFrame.WordWrap=-1
    $r=$s.TextFrame.TextRange;$r.Text=$value;$r.Font.Name=$font;$r.Font.NameFarEast='Microsoft YaHei'
    $r.Font.Size=$size;$r.Font.Color.RGB=$color;$r.Font.Bold=0
    if($bold){$r.Font.Bold=-1}
    $r.ParagraphFormat.SpaceBefore=0;$r.ParagraphFormat.SpaceAfter=0
    $s.TextFrame2.AutoSize=0;$s.Width=$w;$s.Height=$h
    return $s
}
$app=New-Object -ComObject PowerPoint.Application
$d=$app.Presentations.Open($draft,0,0,0)
try{
    # Use a closed, verified template snapshot. Never open or edit the live main PPT.
    for($i=$d.Slides.Count;$i -ge 1;$i--){if($i -ne 7){$d.Slides.Item($i).Delete()}}
    $s=$d.Slides.Item(1)
    for($i=$s.Shapes.Count;$i -ge 1;$i--){if($s.Shapes.Item($i).Name -ne 'Rectangle 1' -and $s.Shapes.Item($i).Top -gt 53){$s.Shapes.Item($i).Delete()}}
    $bar=$s.Shapes.AddShape(1,25,71,9,31);$bar.Name='标题强调条';$bar.Fill.ForeColor.RGB=$purple;$bar.Line.Visible=0
    $null=AddText $s '内容标题' 'RL 电压控制相关研究' 46 67 880 43 28 'Microsoft YaHei' $purple $true
    $groups=@(@{title='基于 RL 的主动配电网电压控制';ids=@(5,6,7)},@{title='多智能体协同电压控制';ids=@(8,9,10,11)},@{title='双时间尺度协调电压控制';ids=@(12,13,14)})
    $y=112.0
    foreach($group in $groups){
        $null=AddText $s ('分类-'+$group.title) $group.title 40 $y 880 24 18 'Microsoft YaHei' $purple $true
        $y+=25
        foreach($id in $group.ids){
            $shape=AddText $s ('完整参考文献-'+$id) $refs[[string]$id] 40 $y 880 65 13.5 'Times New Roman' 2500134
            $tr=$shape.TextFrame.TextRange
            $tr.ParagraphFormat.LineRuleWithin=0;$tr.ParagraphFormat.SpaceWithin=15
            $first=$tr.Text.IndexOf('"');$last=$tr.Text.LastIndexOf('"')
            if($first -ge 0 -and $last -gt $first){$tr.Characters($first+2,$last-$first-1).Font.Bold=-1}
            $needed=[Math]::Ceiling($tr.BoundHeight)+1;$shape.Height=$needed
            $y+=$needed+1
        }
        $y+=2
    }
    if($y -gt 525){throw ('Reference body exceeds slide area: '+$y)}
    $s.NotesPage.Shapes.Placeholders.Item(2).TextFrame.TextRange.Text='按目标论文引言rc-000012分类：[5]–[7]为RL电压控制，[8]–[11]为多智能体控制，[12]–[14]为双时间尺度协调。各条书目完整提取自参考文献，保留原文作者、标题、期刊、卷期、页码和年月。这是独立待插入页，没有打开或修改正在编辑的正式PPT。来源：'+$Paper
    $d.Save()
    $s.Export((Join-Path $BuildDir 'rl-literature.png'),'PNG',1600,900)
    $audit=@()
    foreach($sh in $s.Shapes){if($sh.HasTextFrame -eq -1 -and $sh.TextFrame.HasText -eq -1){$audit+=@{name=$sh.Name;text=$sh.TextFrame.TextRange.Text;width=$sh.Width;height=$sh.Height;boundWidth=$sh.TextFrame.TextRange.BoundWidth;boundHeight=$sh.TextFrame.TextRange.BoundHeight;fontSize=$sh.TextFrame.TextRange.Font.Size}}}
    @{slides=$d.Slides.Count;references=$refs;textObjects=$audit;bodyEnd=$y;mainPptOpened=$false}|ConvertTo-Json -Depth 7|Set-Content -LiteralPath (Join-Path $BuildDir 'verification.json') -Encoding UTF8
    Write-Output ('Draft '+$draft+'; bodyEnd='+$y)
}finally{$d.Close()}
