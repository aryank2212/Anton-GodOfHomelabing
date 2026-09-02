def to_kanji_date(date_obj):
    if not date_obj:
        return ""
        
    kanji_nums = {
        0: '〇', 1: '一', 2: '二', 3: '三', 4: '四', 
        5: '五', 6: '六', 7: '七', 8: '八', 9: '九'
    }
    
    def num_to_kanji(n, is_year=False):
        if is_year:
            return "".join(kanji_nums[int(d)] for d in str(n))
        else:
            if n <= 10:
                return kanji_nums.get(n, "十")
            elif n < 20:
                return "十" + (kanji_nums[n % 10] if n % 10 != 0 else "")
            else:
                tens = n // 10
                ones = n % 10
                return kanji_nums[tens] + "十" + (kanji_nums[ones] if ones != 0 else "")

    day = num_to_kanji(date_obj.day)
    month = num_to_kanji(date_obj.month)
    year = num_to_kanji(date_obj.year, is_year=True)
    
    return f"{day}日 {month}月 {year}"
