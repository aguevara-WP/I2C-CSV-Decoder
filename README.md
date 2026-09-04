# I2C Decode

Adds a `Description` column to a Total Phase Data Center I2C CSV, translating MAX77972 register traffic into human-readable text.

The decoder is Python standard-library only. A virtual environment is recommended but not required.


## Usage

```powershell
python decode_i2c_csv.py "MAX77972 Capture.csv"
```

That writes `MAX77972 Capture_decoded.csv` next to the input. Existing Total Phase columns and any extra notes you added stay as they are; `Description` is inserted after `Data`.

Useful options:

```powershell
python decode_i2c_csv.py "MAX77972 Capture.csv" -o decoded.csv
python decode_i2c_csv.py "MAX77972 Capture.csv" --in-place
python decode_i2c_csv.py "MAX77972 Capture.csv" -m register_map.json
```

- `-o` / `--output` writes a specific output path
- `--in-place` replaces the input file atomically (do not combine with `-o`)
- `-m` / `--map` selects a register-map JSON (default: `register_map.json` beside the script)

## What the decoder does

1. Reads a Total Phase CSV (comment preamble plus `Level, Index, Time, Dur, Len, Err, S/P, Addr, Record, Data`).
2. Tracks the last I2C register pointer written to each 7-bit slave address.
3. Decodes later reads and sequential writes using that pointer. Word addresses auto-increment by one 16-bit word after each word.
4. Looks up the internal offset in `register_map.json`:
   - `0x36` (`FG_FUNC_MAP`): offsets `0x000`–`0x0FF`
   - `0x37` (`FG_DEBUG_MAP`): pointer + 256, so NVM/debug registers sit at `0x100`–`0x1FF`

Descriptions look like:

```
Set pointer DevName (0x021)
Read DevName (0x021) = 0x5030 (MAX77972)
Read nChgConfig5 (0x0D5) = 0x0002 (ChgEnable=1 (charger enabled))
Read VEmpty (0x01F) = 0xA561 (VE=3300 mV, VR=3880 mV)
```

Scaled registers print engineering units. Bitfield registers print named fields, scaled sub-fields, and enum meanings when the map defines them. Characterization tables and undocumented blobs stay as hex.

## Register map

`MAX77972_register_map.json` is the source of names, scales, bitfields, and enums. It follows Analog Devices MAX77972 datasheet Rev 1 (1/25). Registers that the datasheet only documents as raw characterization data (`OCVTable`, `XTable`, `QRTable`, `nRComp0`, `nTempCo`, and similar) remain hex.

To support another device, copy the JSON shape (`addresses`, `registers`, `format`, `fields`) and pass it with `-m`.

