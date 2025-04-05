import React, { useState } from 'react';
import './styles/assignmentgenerator.css';
import { getAuth } from 'firebase/auth';

const AssignmentGenerator = () => {
    const [topic, setTopic] = useState('');
    const [pdfFile, setPdfFile] = useState(null);
    const [difficulty, setDifficulty] = useState('easy');
    const [duration, setDuration] = useState('30');
    const [learningObjectives, setLearningObjectives] = useState('');
    const [additionalRequirements, setAdditionalRequirements] = useState('');
    const [numQuestions, setNumQuestions] = useState(5);
    const [customDuration, setCustomDuration] = useState('');
    const [assignmentOutput, setAssignmentOutput] = useState(null);
    const [isGenerating, setIsGenerating] = useState(false);
    const [errorMessage, setErrorMessage] = useState('');

    const handleGenerate = async (event) => {
        event.preventDefault();
        setIsGenerating(true);
        setAssignmentOutput(null);
        setErrorMessage('');

        // Get authentication token using Firebase
        const auth = getAuth();
        const user = auth.currentUser;
        
        if (!user) {
            setErrorMessage('Please log in to generate an assignment');
            setIsGenerating(false);
            return;
        }

        // Force token refresh to ensure it's valid
        let idToken;
        try {
            idToken = await user.getIdToken(true);
        } catch (error) {
            console.error('Error refreshing token:', error);
            setErrorMessage('Authentication error. Please try logging in again.');
            setIsGenerating(false);
            return;
        }

        const formData = new FormData();
        formData.append('topic', topic);
        formData.append('difficulty', difficulty);
        formData.append('duration', duration === 'custom' ? customDuration : duration);
        formData.append('num_questions', numQuestions);
        formData.append('learning_objectives', learningObjectives);
        formData.append('additional_requirements', additionalRequirements);
        if (pdfFile) {
            formData.append('pdf_file', pdfFile);
        }

        try {
            const response = await fetch('http://localhost:5049/api/generate-assignment/', {
                method: 'POST',
                headers: {
                    'Authorization': `Bearer ${idToken}`
                },
                body: formData,
            });
            
            if (!response.ok) {
                throw new Error(`Server responded with status: ${response.status}`);
            }
            
            const data = await response.json();
            console.log("Response data:", data);
            setAssignmentOutput(data);
            
            // Make the output visible
            document.getElementById('assignment-output').classList.add('visible');
        } catch (error) {
            console.error('Error generating assignment:', error);
            setErrorMessage(`Error: ${error.message || 'Failed to generate assignment'}`);
        } finally {
            setIsGenerating(false);
        }
    };

    // Calculate total marks from the questions
    const calculateTotalMarks = () => {
        if (!assignmentOutput || !assignmentOutput.content || !assignmentOutput.content.questions) {
            return 0;
        }
        
        return assignmentOutput.content.questions.reduce((total, question) => {
            return total + (question.marks || 0);
        }, 0);
    };

    

    const handleDownloadPDF = async (includeAnswers) => {
        if (!assignmentOutput || !assignmentOutput.assignment_id) {
            setErrorMessage('No assignment available to download');
            return;
        }
        
        // Get fresh token for download request
        const auth = getAuth();
        const user = auth.currentUser;
        
        if (!user) {
            setErrorMessage('Please log in to download the assignment');
            return;
        }
        
        try {
            const idToken = await user.getIdToken(true);
            const assignmentId = assignmentOutput.assignment_id;
            const includeAnswersParam = includeAnswers ? 'true' : 'false';
            
            // Use fetch to get the PDF with authentication
            const response = await fetch(
                `http://localhost:5049/api/download-assignment-pdf/?assignment_id=${assignmentId}&include_answers=${includeAnswersParam}`,
                {
                    headers: {
                        'Authorization': `Bearer ${idToken}`
                    }
                }
            );
            
            if (!response.ok) {
                throw new Error(`Server responded with status: ${response.status}`);
            }
            
            // Convert response to blob and download
            const blob = await response.blob();
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `${assignmentOutput.topic.replace(/\s+/g, '_')}_${includeAnswers ? 'full' : 'questions'}.pdf`;
            document.body.appendChild(a);
            a.click();
            window.URL.revokeObjectURL(url);
            document.body.removeChild(a);
            
        } catch (error) {
            console.error('Error downloading PDF:', error);
            setErrorMessage(`Error downloading PDF: ${error.message}`);
        }
    };

    return (
        <div>
            <div className="header">
                <h1>Assignment Generator</h1>
                <p>Create customized assignments with ease</p>
            </div>

            <div className="form-container">
                <form onSubmit={handleGenerate}>
                    <div className="form-grid">
                        <div className="form-group">
                            <label>Topic</label>
                            <input type="text" value={topic} onChange={(e) => setTopic(e.target.value)} required placeholder="Enter assignment topic" />
                        </div>
                        <div className="form-group">
                            <label>Content PDF (Optional)</label>
                            <input type="file" accept=".pdf" onChange={(e) => setPdfFile(e.target.files[0])} />
                        </div>
                        <div className="form-group">
                            <label>Difficulty Level</label>
                            <select value={difficulty} onChange={(e) => setDifficulty(e.target.value)} required>
                                <option value="easy">Easy</option>
                                <option value="medium">Medium</option>
                                <option value="hard">Hard</option>
                            </select>
                        </div>
                        <div className="form-group">
                            <label>Duration</label>
                            <select value={duration} onChange={(e) => setDuration(e.target.value)} required>
                                <option value="30">30 minutes</option>
                                <option value="60">1 hour</option>
                                <option value="90">1.5 hours</option>
                                <option value="120">2 hours</option>
                                <option value="custom">Custom</option>
                            </select>
                        </div>
                        <div className="form-group">
                            <label>Learning Objectives</label>
                            <textarea value={learningObjectives} onChange={(e) => setLearningObjectives(e.target.value)} placeholder="What should students learn from this assignment?"></textarea>
                        </div>
                        <div className="form-group">
                            <label>Additional Requirements (Optional)</label>
                            <textarea value={additionalRequirements} onChange={(e) => setAdditionalRequirements(e.target.value)} placeholder="Any specific requirements or guidelines"></textarea>
                        </div>
                        <div className="form-group">
                            <label>Number of Questions</label>
                            <input type="number" value={numQuestions} min="1" max="20" onChange={(e) => setNumQuestions(parseInt(e.target.value))} required />
                        </div>
                        {duration === 'custom' && (
                            <div className="form-group" id="custom-duration-group" style={{display: 'block'}}>
                                <label>Custom Duration (minutes)</label>
                                <input type="number" value={customDuration} min="1" onChange={(e) => setCustomDuration(e.target.value)} placeholder="Enter duration in minutes" required />
                            </div>
                        )}
                    </div>
                    <div style={{ textAlign: 'center', marginTop: '2rem' }}>
                        <button type="submit" disabled={isGenerating}>{isGenerating ? 'Generating...' : 'Generate Assignment'}</button>
                    </div>
                </form>

                {errorMessage && (
                    <div className="error-message">
                        {errorMessage}
                    </div>
                )}

                <div id="assignment-output">
                    <h2>Generated Assignment</h2>
                    {assignmentOutput && (
                        <div id="assignment-content">
                            <h3>{assignmentOutput.topic}</h3>
                            <p><strong>Duration:</strong> {assignmentOutput.duration}</p>
                            <p><strong>Total Marks:</strong> {calculateTotalMarks()}</p>
                            <div style={{ whiteSpace: 'pre-wrap' }}>
                                {assignmentOutput.content && assignmentOutput.content.questions && 
                                    assignmentOutput.content.questions.map((q, index) => (
                                        <div className="question" key={index}>
                                            <h4>Question {q.question_number}</h4>
                                            <p>{q.question_text}</p>
                                            <p><strong>Answer:</strong> {q.answer}</p>
                                            <p><strong>Marks:</strong> {q.marks}</p>
                                        </div>
                                    ))
                                }
                            </div>
                        </div>
                    )}
                    <div id="export-options">
                        <button onClick={() => handleDownloadPDF(false)}>Download Questions Only</button>
                        <button onClick={() => handleDownloadPDF(true)}>Download Full Assignment</button>
                    </div>
                </div>
            </div>
        </div>
    );
};

export default AssignmentGenerator;
