    .zpvar temp .byte = $80
    .zpvar ptr .word

    org $2000

main
    lda temp
    lda ptr
    rts

    end