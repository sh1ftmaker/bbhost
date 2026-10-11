#!/bin/sh
# usage: dis.sh BN_start BN_stop   (BN = RVA + 0x400000)
OD="/c/Users/shift/AppData/Local/Microsoft/WinGet/Packages/MartinStorsjo.LLVM-MinGW.UCRT_Microsoft.Winget.Source_8wekyb3d8bbwe/llvm-mingw-20260505-ucrt-x86_64/bin/llvm-objdump.exe"
s=$(printf '0x%x' $(( $1 - 0x400000 ))); e=$(printf '0x%x' $(( $2 - 0x400000 )))
"$OD" -d -M intel --adjust-vma=0x400000 --start-address=$s --stop-address=$e /c/Users/shift/Desktop/Bloodborne/games/eboot-109-decrypted.elf 2>/dev/null | tail -n +7 | sed 's/<PT_LOAD#0+0x[0-9a-f]*>//'
