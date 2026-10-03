The Manus 1.6 Lite platform is an AI-driven workspace designed to assist with information retrieval, web browsing, application development, and task management. The interface is characterized by a dark theme with a clean, modern aesthetic, integrating a sidebar for navigation and a centralized workspace for AI interactions.

### **1. Global GUI Layout and Controls**

**A. Header Navigation:**
*   **Logo & Search:** The top left features the "manus" logo and a global search icon.
*   **Plan Information:** A center-top pill displays the current version ("Manus 1.6 Lite") and account status ("Free plan" with an "Upgrade" button).
*   **User Credits:** The top right displays the user's available credits (e.g., 1,299).
*   **Quick Actions:** Icons for "Preview," "Code View," "Project Settings," and "Publish" appear in the top right during active projects.

**B. Left Sidebar (Navigation & Management):**
*   **Core Functions:**
    *   **New Task:** Initiates a fresh interaction window.
    *   **Agent:** Accesses a personalized AI agent profile where users can configure their digital assistant.
    *   **Skills (New):** A marketplace-style library of specialized AI capabilities like "Alternative Blog Writer," "SEO Audit Report Generator," and "AI Video Generator."
    *   **Plugins:** Manages third-party integrations and connectors (e.g., Google Calendar, Shopify, Stripe).
    *   **Scheduled:** A calendar view for setting up automated, recurring tasks.
    *   **Library:** A central repository for all generated assets, filterable by type (Slides, Websites, Documents, Spreadsheets, Images, etc.).
*   **Project Management:**
    *   **New Project:** Button to start a grouped set of tasks.
    *   **Recent Tasks/Projects:** A scrollable list allowing quick navigation between previous work (e.g., "Veloura Cosmetics," "Morning greeting").

**C. Main Workspace (Interaction Area):**
*   **Input Bar:** A central multi-functional bar for typing prompts. It includes:
    *   **Add File (+):** For uploading documents or images (as seen in the math problem solving workflow).
    *   **GitHub Integration:** Direct link to sync projects.
    *   **Environment Toggle:** Dropdown to select the operating environment (e.g., "Manus Desktop").
*   **Feature Shortcuts:** Below the input bar, quick-start buttons for "Create slides," "Build website," "Design," "Create games," and "More."

---

### **2. Key Workflows and Demonstrations**

#### **Information Retrieval and Web Browsing**
*   **Interaction:** The user inputs a URL (e.g., a religious website or a game repack site).
*   **Execution:** The AI enters a "Working" state, showing real-time status updates like "Browsed 1 page" and "Reviewing main content."
*   **Output:** Manus provides structured summaries, lists of items found, and promotional links.
*   **Visual Confirmation:** A "Manus's computer" side-panel can be opened to view the actual live rendering of the browsed webpage alongside the AI's analysis.

#### **Application Development (Veloura Cosmetics)**
*   **Prompting:** The user requests the creation of a web app for selling cosmetics.
*   **Configuration:** The AI asks follow-up questions regarding the app's function, target audience, platform (Web, Mobile, Desktop), and branding.
*   **Automated Building:** Manus runs background commands, creates original imagery, defines a visual storefront structure, and configures backend components.
*   **Live Preview:** A side-by-side "Preview" pane allows users to interact with the developing app in real-time. This preview supports responsive design testing (Desktop vs. Mobile views).
*   **Project Dashboard:**
    *   **Canvas:** A visual editor for the UI.
    *   **Dashboard:** Provides analytics and site overview.
    *   **Database:** View and manage user data and product tables.
    *   **File Storage:** A file manager showing uploaded assets (e.g., product images for "Veloura").
    *   **Settings:** Extensive configuration for domains, SEO, secrets (API keys), and integrations (Stripe/Shopify).

#### **Multimodal Problem Solving**
*   **Input:** The user uploads an image containing a complex geometry problem.
*   **Analysis:** Manus uses visual recognition to identify the problem type (rectangular solid) and relevant labels ($AG$, $JC$, $GF$, etc.).
*   **Solution:** It provides a step-by-step mathematical breakdown, including the setting up of equations, substitution, and final numerical answers.

#### **Localized Research Task**
*   **Scenario:** The user asks to buy a new car in Ghana.
*   **Dynamic Response:** The AI requests the specific country if not provided, then executes a targeted search for official dealerships.
*   **Results:** It generates a categorized list of dealers (Toyota, Kia, Mercedes-Benz) including physical addresses, website links, contact numbers, and specific car models available in that region.

---

### **3. Technical Requirements for Reproduction**
To reproduce the workflows shown, the system requires:
*   **Web Browsing Capability:** Access to live internet data for summarizing external sites.
*   **Development Stack:** A containerized environment capable of running web development commands and hosting a live preview of React/Next.js-style applications.
*   **Computer Vision:** An OCR and image analysis engine to interpret uploaded screenshots and handwritten/printed documents.
*   **Integration APIs:** Connectors for third-party services like GitHub (for version control) and Stripe/Shopify (for e-commerce functionality).
*   **Credit System:** A backend to track and deduct user credits based on task complexity.