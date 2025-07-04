if(FALSE) # fake a block comment
add_library(eeprom)
target_sources(eeprom INTERFACE
    eeprom/ee.c
    eeprom/ee.h
)
target_include_directories(eeprom INTERFACE
    eeprom
)

add_library(LCD)
target_sources(LCD INTERFACE
    LCD/LCD.c
    LCD/LCD.h
)
target_include_directories(LCD INTERFACE
    LCD
)

target_link_libraries(LCD stm32cubemx)
target_link_libraries(eeprom stm32cubemx)
    
add_subdirectory(stm32/cmake/stm32cubemx)
endif()

add_library(eepromh5)
target_sources(eepromh5 INTERFACE
    eeprom/ee.c
    eeprom/ee.h
)
target_include_directories(eepromh5 INTERFACE
    eeprom
)

add_library(LCDh5)
target_sources(LCDh5 INTERFACE
    LCD/LCD.c
    LCD/LCD.h
)
target_include_directories(LCDh5 INTERFACE
    LCD
)

target_link_libraries(LCDh5 stm32cubemxh5)
target_link_libraries(eepromh5 stm32cubemxh5)
    
add_subdirectory(stm32h5/cmake/stm32cubemx)
