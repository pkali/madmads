.enum   @dmactl
    blank   =    %00
    narrow  =    %01
    standard=    %10
    wide    =    %11
    missiles=   %100
    players =  %1000
    lineX1  = %10000
    lineX2  = %00000
    dma =    %100000
.ende

scr48   = @dmactl(wide|dma|players|missiles|lineX1)

    org $2000
    .byte scr48