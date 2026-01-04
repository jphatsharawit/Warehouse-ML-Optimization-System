
# Warehouse Slotting Optimization System (Experimental)

![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)
![React](https://img.shields.io/badge/React-Vite-61DAFB.svg)
![FastAPI](https://img.shields.io/badge/FastAPI-0.95+-009688.svg)
![Status](https://img.shields.io/badge/Status-Proof%20of%20Concept-orange)

## Introduction

This project is a Proof of Concept (PoC) for an intelligent warehouse inventory placement system.

Developing a warehouse optimization system typically requires access to extensive proprietary operational data. To overcome **real-world data accessibility constraints**, this project adopts a simulation-based approach utilizing synthetic data generation.

The repository features a self-implemented hybrid engine that combines **Gravity Models** (distance minimization) and **Machine Learning** (K-Means Clustering) to solve slotting optimization problems. This system demonstrates how logistics logic can be engineered, tested, and validated within a controlled, data-scarce environment.

---

## Key Features

### 1. Hybrid Optimization Engine
Calculates optimal slot assignments for each SKU by analyzing order frequency (ABC Analysis) and physical distance from the packing station. The algorithm balances picking velocity with storage efficiency.

### 2. 3D Warehouse Mapping
Supports complex warehouse layouts, including defined Aisles, Vertical Levels, and Bins. The system dynamically maps grid coordinates to physical locations for precise distance calculation.

### 3. Path Simulation
Implements a traversal picking strategy simulation to quantify efficiency gains. The system calculates and compares total travel distance between the pre-optimization and post-optimization states.

### 4. Automated Data Sanitization
Features a robust data processing pipeline that automatically detects and resolves common dataset anomalies, such as missing values, infinite values, and schema inconsistencies, ensuring system stability during execution.

### 5. Automated Environment Setup
Includes a Windows Batch Script (`clean_start(Port .8000).bat`) for automated environment configuration, dependency installation, and server initialization.

---

## Technical Architecture

* **Backend:** Python, FastAPI, Pandas, NumPy, Scikit-learn
* **Frontend:** React.js, Vite, Recharts (Data Visualization)
* **Automation:** Windows Batch Scripting

---

## Project Structure

```text
warehouse-optimization-ai/
│
├── project/                # Backend Application (FastAPI)
│   ├── core.py             # Optimization Logic & Algorithms
│   ├── app.py              # API Endpoints
│        └── ...
├── warehouse-ui/           # Frontend Application (React)
│       └── ...
├── Data sets/              # Generated Datasets
│   ├── warehouse_map.json  # Physical Layout Configuration
│   ├── inventory.csv       # Current Stock Snapshot
│   └── data_SC*.csv        # Simulation Scenarios
├── clean_start(Port .8000).bat 
├── .gitignore
└── README.md
```
## Getting Started

This section outlines the prerequisites and steps required to set up the development environment and execute the application locally.

### Prerequisites

Ensure the following software is installed on your local machine before proceeding:

* **Python 3.10** or higher (Ensure Python is added to the system PATH during installation).
* **Node.js** (LTS version recommended for frontend compilation).
* **Git** (For version control).

## 💡 Key Takeaways & Project Insights

This project served as a comprehensive sandbox for advanced software engineering and data science concepts. Key learning outcomes include:

### 1. Overcoming Data Scarcity
The biggest challenge was the lack of real-world datasets. This constraint drove the development of a **custom synthetic data generator**, proving that algorithmic logic can be successfully tested and validated using simulated environments. It bridged the gap between theoretical models and practical application.

### 2. Algorithmic Problem Solving
Implementing the optimization engine required translating physical warehouse constraints (3D coordinates, gravity models) into code. This involved designing custom heuristics to balance **picking velocity** against **storage density**, going beyond simple database queries.

### 3. Resilient Data Engineering
Real-world data is rarely clean. Developing the **"God Mode" data sanitizer** highlighted the importance of defensive programming. The system was engineered to handle edge cases—such as missing values, infinite integers, and schema mismatches—without crashing, ensuring system stability.

### 4. End-to-End System Integration
This project successfully demonstrated the integration of disparate technologies: connecting a mathematical Python backend with a reactive frontend UI, and wrapping the entire complex environment into a single, user-friendly **Batch Script** for automated deployment.
