# MSDN to Docset Converter

## Overview

This tool converts Microsoft Documentation (MSDN) for Windows Win32 API into a searchable offline documentation package
in the Dash/Zeal docset format. It allows developers to access Microsoft's Windows API documentation locally without an
internet connection.

## Features

- Downloads and processes Windows Win32 API documentation from Microsoft Docs
- Includes documentation for:
  - Win32 API functions
  - Interfaces
  - Structures
  - Enums
  - Classes
  - SDK API reference

## Setup

1. Create `virtualenv` with something like `virtualenv --python <PATH_TO_PYTHON.EXE> venv`.
2. Activate `venv` with `& .\venv\Scripts\activate.ps1`
3. Install dependencies with `pip install -r requirements.txt`

```pwsh
# EXAMPLE

# Create `venv`
virtualenv --python 'C:\Program Files (x86)\Microsoft Visual Studio\Shared\Python39_64\python.exe' venv

# Activate `venv`
& .\venv\Scripts\activate.ps1

# Install dependencies
pip install -r requirements.txt
```

## Create Docset

> NOTE: This can take a few hours

```pwsh
# NOTE: This can take a few hours
> python .\msdn-to-docset.py create_docset

# CONFIRM 
> ls
Mode                LastWriteTime         Length Name
----                -------------         ------ ----
-a---         4/12/2025   5:04 PM      504385566   MSDN.tgz

```

## Install Docset

### Windows

```pwsh

# SET LOCAL VARIABLE TO THE ARCHIVE PATH 
$msdnDocsetPath = (Resolve-Path MSDN.tgz)

# CD TO ZEAL DOCSETS DIR
cd C:\Users\<USERNAME>\AppData\Local\Zeal\Zeal\docsets

# UNPACK THE DOCSET
tar xvfz $msdnDocsetPath

```

## Credits

@lucasg - all the heavy lifting
@jglanz (me) - I just cleaned it up, added error handling and a README, etc